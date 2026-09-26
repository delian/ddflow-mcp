"""The ddflow command line — the portable surface every agent can drive.

Design commitments, each of which exists so that ANY agent (or a human, or CI) can use
this without special support:

* **Everything is available as a subprocess call.** MCP is an optional convenience
  layer over the same functions, never a requirement. An agent with nothing but a shell
  can run the entire workflow.
* **``--json`` on every read command.** Agents parse; humans read. Offering both from
  one code path stops the two drifting.
* **A uniform exit vocabulary:** ``0`` healthy · ``1`` real failure · ``2`` could not
  run / nothing to do · ``3`` coordination refused. ``2`` is never collapsed into ``0``.
  "Nothing is ready" and "everything is fine" are different facts and an agent that
  cannot tell them apart will invent work.
* **Refusals name the alternative.** A command that says no says what to do instead;
  that is the difference between a blocked agent and a re-ordered one.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ..api import items as A_ITEMS
from ..api import lifecycle as A_LIFECYCLE
from ..api import reporting as A_REPORTING
from ..config import Config
from ..core.model import GATE_OUTCOMES, fold
from ..infra import tomlcfg as TC
from ..infra import worktree as W
from ..services import gates as G
from ..services import leases as L
from .commands.config import (  # noqa: F401  -- moved out of this module
    _config_set,
    _workflow_problems,
    _write_config,
)
from .commands.decisions import cmd_decision
from .commands.gates import cmd_gate
from .commands.knowledge import (
    cmd_bug,
    cmd_history,
    cmd_lesson,
    cmd_recall,
    cmd_research,
    cmd_session,
)
from .commands.lifecycle import (
    cmd_abandon,
    cmd_block,
    cmd_brief,
    cmd_claim,
    cmd_complete,
    cmd_heartbeat,
    cmd_merge,
    cmd_next,
    cmd_release,
    cmd_remove,
)
from .commands.queue import cmd_phase_add, cmd_split, cmd_task_add
from .commands.reporting import (
    cmd_board,
    cmd_doctor,
    cmd_rebuild,
    cmd_recover,
    cmd_render,
    cmd_replay,
    cmd_show,
    cmd_status,
)
from .commands.review import cmd_review, cmd_reviewers
from .commands.workflow import cmd_workflow
from .context import (
    FAIL,
    NOTHING,
    OK,
    REFUSED,
    Ctx,
    _csv,
    _plain,
    _require_item,
)


def cmd_init(a, c: Ctx) -> int:
    d = c.repo / ".ddflow"
    d.mkdir(parents=True, exist_ok=True)
    (d / "events").mkdir(exist_ok=True)
    gi = d / ".gitignore"
    gi.write_text(
        "# The index and local state are DERIVED from events/ and are rebuildable.\n"
        "# They are machine-local on purpose: a committed index resurrects dead agents'\n"
        "# leases on every clone, and a committed cache is a merge conflict with no\n"
        "# meaningful resolution.\n"
        "index.db\nindex.db-*\nindex.rebuilding*\nevents.lock\nlocal/\n",
        "utf-8",
    )
    cfgp = d / "config.toml"
    if not cfgp.exists():
        cfgp.write_text(_starter_config(), "utf-8")
    # An in-repo worktree root (the default inside a container, where a sibling path
    # would land on the ephemeral layer) must be ignored, or every worktree shows up as
    # hundreds of untracked files and the enforcement hook trips over them.
    root_gi = c.repo / ".gitignore"
    prev_gi = root_gi.read_text("utf-8") if root_gi.exists() else ""
    if ".ddflow-worktrees" not in prev_gi:
        root_gi.write_text(
            prev_gi
            + ("" if prev_gi.endswith("\n") or not prev_gi else "\n")
            + "\n# ddflow task worktrees (git worktrees; never commit them)\n"
            ".ddflow-worktrees/\n",
            "utf-8",
        )

    ga = c.repo / ".gitattributes"
    line = ".ddflow/events/*.jsonl merge=union\n"
    prev = ga.read_text("utf-8") if ga.exists() else ""
    if "ddflow/events" not in prev:
        ga.write_text(prev + ("" if prev.endswith("\n") or not prev else "\n") + line, "utf-8")
    c.store.rebuild(c.log)
    # Setup touches TRACKED files (.gitignore, .gitattributes, AGENTS.md). Leaving them
    # uncommitted makes the primary checkout dirty, and `ddflow merge` then refuses --
    # correctly, but with a message about "modified tracked files" that gives no hint
    # the cause was ddflow's own setup two commands ago. Say so here instead.
    touched = [
        rel
        for rel in (
            ".gitignore",
            ".gitattributes",
            ".ddflow",
            "AGENTS.md",
            "CLAUDE.md",
            "docs/ddflow",
        )
        if (c.repo / rel).exists() and W.git(c.repo, "status", "--porcelain", "--", rel).out.strip()
    ]
    commit_hint = ""
    if touched:
        commit_hint = (
            "\n\n  Setup changed these files — commit them before your first merge, or "
            "the primary\n  checkout stays dirty and `ddflow merge` will refuse:\n"
            f"    git add {' '.join(touched)} && git commit -m 'ddflow: adopt'"
        )
    c.out(
        f"Initialised ddflow in {d}\n"
        f"  config: {cfgp}\n"
        f"  Next: `ddflow phase add P1 --title 'First phase'`" + commit_hint,
        {"root": str(d), "config": str(cfgp)},
    )
    return OK


def cmd_item_update(a, c: Ctx) -> int:
    st = c.state()
    it = _require_item(c, a.id, st)
    if it is None:
        return FAIL
    d: dict[str, Any] = {}
    for f in ("title", "body"):
        if getattr(a, f) is not None:
            d[f] = getattr(a, f)
    for f in ("needs", "globs", "tags"):
        if getattr(a, f) is not None:
            d[f] = _csv(getattr(a, f))
    if a.priority is not None:
        d["priority"] = a.priority
    if not d:
        print("nothing to update", file=sys.stderr)
        return NOTHING
    c.log.append("phase.updated" if it.kind == "phase" else "task.updated", a.id, d)
    c.out(f"{a.id} updated: {', '.join(d)}", {"id": a.id, "changed": list(d)})
    return OK


def cmd_approve(a, c: Ctx) -> int:
    """A person clears, or refuses, a human-approval gate.

    CLI ONLY, on purpose, and `tests/test_mcp_parity.py` records the exemption with its
    reason. A human checkpoint an agent can satisfy through the MCP surface is not a
    human checkpoint — it is a second `gate record` with a longer name.
    """
    # `_require_item` FIRST, like every sibling. Without it `approve` was the one
    # gate-writing path with no existence check: `_h_gate` folds through `_item`, which
    # CREATES an item for an unknown subject, so a typo'd id printed "approved", exited
    # 0, and materialised a phantom task carrying a human approval -- while the item the
    # operator meant to approve stayed unapproved. That is the exact opposite of what
    # this command is for.
    if _require_item(c, a.id) is None:
        return FAIL
    try:
        line = G.approve(
            c.log,
            c.cfg,
            a.id,
            a.gate,
            gates=c.gates,
            note=a.note or "",
            reject=bool(a.reject),
            reason=a.reason or "",
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return FAIL
    c.out(line, {"id": a.id, "gate": a.gate, "approved": not a.reject, "line": line})
    return OK


def cmd_progress(a, c: Ctx) -> int:
    """What work has actually been done, aggregated from the log."""
    from ..api import progress as _progress
    from ..core import progress as PR

    out = _progress(c.repo, a.id or "")
    if out.exit == FAIL:
        print(out.reason, file=sys.stderr)
        return FAIL
    if c.json:
        # The ROW ARRAY, unchanged. `ddflow progress --json` has always emitted a list
        # and callers index it; the Outcome carries more, and the `payload` entry on the
        # MCP tool keeps that surface identical too.
        print(json.dumps(out.data["rows"], indent=2, default=str))
        return OK

    rows_d = out.data["rows"]
    if not rows_d:
        print("No work recorded yet.")
        return NOTHING
    # The TABLE renders from the wire rows -- no second fold. `summary()` carries every
    # column: counts for attempts/commits, seconds held, the holder list.
    print(f"{'item':<14} {'state':<10} {'att':>3} {'held':>9} {'gates':>5} {'commits':>7}  holders")
    for d in rows_d:
        secs = d["held_seconds"]
        held = f"{secs / 60:.1f}m" if secs else "-"
        print(
            f"{d['item']:<14} {d['state']:<10} {d['attempts']:>3} {held:>9} "
            f"{d['gate_runs']:>5} {d['commits']:>7}  "
            f"{', '.join(sorted(set(d['holders']))) or '-'}"
        )
    if a.id:
        # The ONLY path that folds again, and only for one item. The per-attempt detail
        # (who held it, how it ended, gates passed and failed) is richer than the wire
        # summary, which flattens `attempts` to a count -- so this needs the objects and
        # the table above does not. Scoped to `--id` rather than paid on every call.
        events = c.log.read_all()
        rows = [r for r in PR.work(events, fold(events, strict=False)).values() if r.item == a.id]
        r = rows[0]
        print(f"\n{r.item} — {r.title}")
        for i, att in enumerate(r.attempts, 1):
            print(
                f"  attempt {i}: {att.holder} · {att.seconds / 60:.1f}m · "
                f"ended {att.ended_by or 'still open'} · "
                f"{att.gates_passed} passed / {att.gates_failed} failed"
            )
        for gate, outcomes in sorted(r.gate_outcomes.items()):
            print(f"  gate {gate:<14} {' -> '.join(outcomes)}")
    total = sum(d["held_seconds"] for d in rows_d)
    print(
        f"\n{len(rows_d)} item(s) · {total / 3600:.1f} agent-hours recorded · "
        f"{sum(d['commits'] for d in rows_d)} commit(s)"
    )
    return OK


def cmd_loops(a, c: Ctx) -> int:
    """Report circular references and runtime loops. Exit 2 when there are none.

    Renders the SAME `Outcome` the MCP surface returns, rather than computing a second
    view of the same answer. That is the B37 shape: one description of a result, two
    presentations derived from it — not two presentations kept in step by hand.
    """
    from ..api import loops as _loops
    from ..core import progress as PR

    out = _loops(c.repo)
    if c.json:
        print(json.dumps(out.data["findings"], indent=2))
        return out.exit
    if not out.data["findings"]:
        print(
            f"No loops detected ({out.data['events']} events, "
            f"{out.data['items']} items).\nChecked: " + ", ".join(out.data["checked"]) + "."
        )
        return out.exit
    # Reconstructed from the dicts the Outcome already carries. Calling `PR.detect`
    # again here would re-read and re-FOLD the whole log for a second copy of an answer
    # already in hand -- turning one O(events) pass into two, on the surface whose
    # entire job is to render what the layer below computed.
    for f in (PR.LoopFinding(**d) for d in out.data["findings"]):
        print(f"\n{f.render()}")
    print(
        f"\n{out.reason}. Thresholds are [loops] knobs; `ddflow config --explain --filter loops`."
    )
    return out.exit


def cmd_cleanup(a, c: Ctx) -> int:
    """Classify every ddflow worktree and branch; with --apply, land the safe ones."""
    from ..services import cleanup as CL

    st = c.store.ensure(c.log)
    plan = CL.survey(c.repo, c.cfg, st)
    if c.json and not a.apply:
        print(
            json.dumps(
                {
                    "trees": [_plain(t) for t in plan.trees],
                    "stale_branches": [_plain(t) for t in plan.stale_branches],
                },
                indent=2,
                default=str,
            )
        )
        return OK if (plan.trees or plan.stale_branches) else NOTHING
    if not plan.trees and not plan.stale_branches:
        print("Nothing to clean up: no ddflow worktrees or branches remain.")
        return NOTHING
    for t in plan.trees + plan.stale_branches:
        print("  " + t.render())
    if plan.needs_human:
        print(
            f"\n{len(plan.needs_human)} tree(s) hold UNCOMMITTED work and are never "
            f"touched automatically. Inspect each before deciding."
        )
    if not a.apply:
        actionable = plan.actionable
        print(
            f"\n{len(actionable)} safe action(s) available. Re-run with --apply to "
            f"perform them; dirty trees are excluded whatever you pass."
        )
        return OK
    done = CL.apply(c.repo, c.cfg, plan)
    for line in done:
        print(f"  {line}")
    c.out(f"\n{len(done)} action(s) performed.", {"performed": done})
    return OK


def cmd_config(a, c: Ctx) -> int:
    if a.set:
        return _config_set(a, c)
    if a.append_toml:
        # Validated BEFORE writing: an agent composing TOML gets a parse error back as
        # a readable message instead of leaving the project with a config that no
        # later command can load.
        import tomllib

        try:
            tomllib.loads(a.append_toml)
        except tomllib.TOMLDecodeError as exc:
            print(f"not valid TOML: {exc}", file=sys.stderr)
            return FAIL
        path = c.repo / ".ddflow" / "config.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        prev = path.read_text("utf-8") if path.exists() else ""
        merged = prev.rstrip() + "\n\n" + a.append_toml.strip() + "\n"
        # The MERGED text, semantically. This used to validate `Config.load(c.repo)` --
        # the config already on DISK -- and then only the SYNTAX of the merge, so an
        # unknown section or knob was written and every later command failed to load
        # the file. A writer that validates the state it is replacing has checked
        # nothing.
        try:
            Config.check(tomllib.loads(merged))
        except (tomllib.TOMLDecodeError, ValueError) as exc:
            print(f"appending this would break the config: {exc}", file=sys.stderr)
            return FAIL
        TC.atomic_write(path, merged)
        c.out(f"appended to {path}", {"path": str(path)})
        return OK
    rows = c.cfg.explain()
    if c.json:
        print(
            json.dumps(
                [{"key": k, "value": v, "source": s, "doc": d} for k, v, s, d in rows],
                indent=2,
                default=str,
            )
        )
        return OK
    for k, v, s, d in rows:
        if a.filter and a.filter not in k:
            continue
        print(f"{k} = {v!r}   [{s}]")
        if a.explain and d:
            for line in _wrap(d, 76):
                print(f"    {line}")
    return OK


def cmd_cadence(a, c: Ctx) -> int:
    """Which periodic passes are due? Derived from the log, so there is no state file."""
    st = c.state()
    done_tasks = sum(1 for i in st.items.values() if i.kind == "task" and i.state == "done")
    done_phases = sum(1 for i in st.items.values() if i.kind == "phase" and i.state == "done")
    due = []
    for name, every, unit, count in (
        ("integration_tests", c.cfg.cadence.integration_tests_every_tasks, "tasks", done_tasks),
        ("dedupe_sweep", c.cfg.cadence.dedupe_sweep_every_tasks, "tasks", done_tasks),
        (
            "architecture_review",
            c.cfg.cadence.architecture_review_every_phases,
            "phases",
            done_phases,
        ),
        ("mutation_tests", c.cfg.cadence.mutation_tests_every_phases, "phases", done_phases),
        ("lessons_pass", c.cfg.cadence.lessons_pass_every_phases, "phases", done_phases),
    ):
        runs = st.cadences.get(name, [])
        at_last = int(runs[-1].get("result", "0") or 0) if runs else 0
        since = count - at_last
        if every > 0 and since >= every:
            due.append({"cadence": name, "since": since, "every": every, "unit": unit})
    due += _lessons_cadence(st, c.cfg)
    if a.ran:
        if a.ran == "lessons_compression":
            live = [x for x in st.lessons.values() if not x.superseded_by]
            result = json.dumps(
                {"bytes": sum(len(x.text().encode("utf-8")) for x in live), "entries": len(live)}
            )
        else:
            result = str(
                done_tasks if a.ran in ("integration_tests", "dedupe_sweep") else done_phases
            )
        c.log.append("cadence.ran", a.ran, {"result": result, "evidence": {"note": a.note or ""}})
        c.out(f"recorded cadence run: {a.ran}", {"cadence": a.ran})
        return OK
    if c.json:
        print(json.dumps(due, indent=2))
        return OK if due else NOTHING
    if not due:
        print(f"No cadence due ({done_tasks} tasks, {done_phases} phases completed).")
        return NOTHING
    for d in due:
        print(f"DUE: {d['cadence']} — {d['since']} {d['unit']} since last (every {d['every']})")
    print("\nRecord one with: ddflow cadence --ran <name>")
    return OK


def _prompt_overrides(c: Ctx) -> dict[str, str]:
    """Thin adapter. The map is built by `services.prompts.overrides_from(cfg)`, which is
    where the api layer can reach it too."""
    from ..services import prompts as P

    return P.overrides_from(c.cfg)


def cmd_prompts(a, c: Ctx) -> int:
    from ..services import prompts as P

    ov = _prompt_overrides(c)
    if a.prompts_cmd == "list":
        rows = P.list_all(c.repo, ov)
        if c.json:
            print(
                json.dumps(
                    [
                        {
                            "name": t.name,
                            "kind": t.kind,
                            "source": t.source,
                            "path": str(t.path),
                            "chars": len(t.text),
                        }
                        for t in rows
                    ],
                    indent=2,
                )
            )
            return OK
        # Grouped, because the two kinds are used for entirely different things: a
        # template is machinery (the review prompt, the gate instruction, the MCP
        # handshake) and a command is a workflow an operator invokes. A flat list of
        # eleven names invites `prompts show mcp_instructions` expecting a workflow.
        for title, kind in (("Templates", "template"), ("Workflow commands", "command")):
            members = [t for t in rows if t.kind == kind]
            if not members:
                continue
            print(f"{title}:")
            for t in members:
                print(f"  {t.name:<24} [{t.source:<7}] {t.path}")
            print()
        print(
            "Edit any of them with `ddflow prompts eject <name>`, which copies the "
            "shipped default into .ddflow/prompts/ where it takes precedence.\n"
            "Read one with `ddflow prompts show <name>`."
        )
        return OK
    if a.prompts_cmd == "show":
        try:
            # `resolve_any`, not `resolve`: a caller should not have to know which of
            # the two registries a name lives in before it can be read. Knowing was
            # what leaked out as "unknown template 'research-companions'" -- a message
            # that listed the five templates to someone who had typed a real command
            # name correctly.
            print(P.resolve_any(a.name, c.repo, ov).text)
        except P.TemplateError as exc:
            print(str(exc), file=sys.stderr)
            return FAIL
        return OK
    if a.prompts_cmd == "eject":
        # With no name, everything -- BOTH registries. Writing only the templates is
        # the silent half of the bug this branch just fixed: `eject` is the documented
        # way to customise a prompt, so covering half the library means the documented
        # way to edit a workflow command did not exist.
        names = [a.name] if a.name else [*P.TEMPLATE_NAMES, *P.COMMANDS]
        out = c.repo / ".ddflow" / "prompts"
        out.mkdir(parents=True, exist_ok=True)
        (out / "commands").mkdir(parents=True, exist_ok=True)
        written = []
        for n in names:
            try:
                t = P.resolve_any(n, None, {})  # the SHIPPED default, not the override
            except P.TemplateError as exc:
                print(str(exc), file=sys.stderr)
                return FAIL
            # A command must land in `prompts/commands/`, which is where
            # `resolve_command` looks. Writing it beside the templates would produce a
            # file the operator edits and the tool never reads -- an override that
            # silently does nothing, which is worse than refusing to eject it.
            dst = (out / "commands" / f"{n}.md") if t.kind == "command" else (out / f"{n}.md")
            if dst.exists() and not a.force:
                print(f"  skipped {dst} (exists; --force to overwrite)")
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(t.text, "utf-8")
            written.append(str(dst))
        c.out(
            "\n".join(f"  wrote {w}" for w in written)
            + "\n\nThese now take precedence over the shipped defaults. Edit them "
            "freely; they are plain text and are not parsed as code.",
            {"written": written},
        )
        return OK
    return FAIL


def cmd_hooks(a, c: Ctx) -> int:
    from ..services import enforce as E

    if a.hooks_cmd == "install":
        msg = E.install(c.repo, force=a.force)
        c.out(msg, {"message": msg})
        return FAIL if msg.startswith("REFUSED") else OK
    if a.hooks_cmd == "uninstall":
        msg = E.uninstall(c.repo)
        c.out(msg, {"message": msg})
        return OK
    if a.hooks_cmd == "status":
        on = E.installed(c.repo)
        mode = c.cfg.enforce.commit_without_lease
        c.out(
            f"pre-commit hook: {'installed' if on else 'NOT installed'}\n"
            f"policy [enforce].commit_without_lease = {mode!r}"
            + (
                "\n\nNOTE: the hook is installed but the policy is 'warn', so it "
                "reports and allows. Set it to 'block' to refuse."
                if on and mode == "warn"
                else ""
            )
            + (
                "\n\nNOTE: the policy is 'block' but NO HOOK IS INSTALLED, so nothing "
                "enforces it. Run `ddflow hooks install`."
                if not on and mode == "block"
                else ""
            ),
            {"installed": on, "policy": mode},
        )
        return OK if on or mode == "off" else NOTHING
    if a.hooks_cmd == "check-commit":
        code, msg = E.check_commit(c.repo, c.cfg)
        if msg:
            print(msg, file=sys.stderr)
        if code == 0 and c.cfg.enforce.require_item_trailer:
            tcode, tmsg = E.check_item_trailer(c.repo)
            if tmsg:
                print(tmsg, file=sys.stderr)
            return tcode
        return code
    return FAIL


def _scan_companions(repo: Path, *, probe: bool = True):
    """Scan, or report why not and return None. ONE guard, used by every caller.

    The registry loader writes a careful sentence naming the file, the id and the bad
    value; letting its `ValueError` escape renders that sentence as an uncaught
    exception and makes the exit code an accident rather than a contract. Guarding it
    per call site meant two of three sites were fixed and `adopt` — documented as safe
    to re-run — still exited on a traceback, after `init` had already written files.
    """
    from ..services import companions as CO

    try:
        return CO.scan(repo, probe=probe)
    except ValueError as exc:
        print(f"{exc}\n  (in .ddflow/companions.toml or .ddflow/config.toml)", file=sys.stderr)
        return None


def cmd_companions(a, c: Ctx) -> int:
    """Which companion MCP servers serve this project's gates, and what is missing.

    Exit 0 when every default companion is registered; exit 2 when something is
    installed-but-unregistered or absent — "no data" reported as itself, never
    collapsed into "no problem". Never exits 1: a missing optional server is a gap to
    close, not a failure of this command.
    """
    from ..services import companions as CO

    statuses = _scan_companions(c.repo, probe=not getattr(a, "no_probe", False))
    if statuses is None:
        return FAIL
    by_id = {st.companion.id: st for st in statuses}

    if a.companions_cmd == "add":
        wanted = _csv(a.id) or [
            st.companion.id
            for st in statuses
            if st.companion.default and st.installed and st.companion.is_mcp  # servers only
        ]
        unknown = [w for w in wanted if w not in by_id]
        if unknown:
            print(
                f"unknown companion(s): {', '.join(unknown)}; known: {', '.join(by_id)}",
                file=sys.stderr,
            )
            return FAIL
        not_servers = [w for w in wanted if not by_id[w].companion.is_mcp]
        if not_servers:
            print(
                "not an MCP server: "
                + "; ".join(
                    f"{w} is a {by_id[w].companion.kind} tool ({by_id[w].companion.install})"
                    for w in not_servers
                )
                + ". There is no MCP config entry to write. Install it and ddflow "
                "detects it; `ddflow companions` shows it either way.",
                file=sys.stderr,
            )
            return REFUSED
        if not wanted:
            print(
                "nothing to add: no default MCP companion is installed on this "
                "machine. `ddflow companions` lists them with their install commands.",
                file=sys.stderr,
            )
            return NOTHING
        # Registering a server that is not installed would write a config entry whose
        # launch fails at the worst moment -- mid-task, as an agent reaches for the
        # tool a gate told it to use. `--force` exists for the case where the operator
        # is about to install it.
        # `is False` and `is None` are different refusals. Both block -- registering a
        # launch command that fails mid-task is the thing to avoid either way -- but
        # "not installed here" sent to an operator whose probe merely TIMED OUT makes
        # them install something they already have, and the install line printed
        # helpfully below then does nothing. Say which one it is.
        absent = [w for w in wanted if by_id[w].installed is False and not a.force]
        unknown = [w for w in wanted if by_id[w].installed is None and not a.force]
        if absent or unknown:
            if absent:
                print(
                    f"not installed here: {', '.join(absent)}. Registering one would "
                    f"write a launch command that fails mid-task. Install it first "
                    f"({'; '.join(by_id[w].companion.install for w in absent)}), or "
                    f"--force if you are about to.",
                    file=sys.stderr,
                )
            if unknown:
                print(
                    f"could not tell whether these are installed: "
                    f"{', '.join(unknown)} — "
                    + "; ".join(by_id[w].detail for w in unknown)
                    + ". That is not the same as absent. Re-run, check by hand, or "
                    "--force if you know it is there.",
                    file=sys.stderr,
                )
            return REFUSED
        agents = _csv(a.agents) or ["claude"]
        dry = bool(getattr(a, "dry_run", False))
        results = [
            CO.register(c.repo, by_id[w].companion, ag, dry_run=dry)
            for w in wanted
            for ag in agents
        ]
        actions = [msg for _st, msg in results]
        # A REFUSAL is not a success. `register` used to return only the message, so an
        # unparseable `.mcp.json` printed "SKIPPED ... not valid JSON", reported
        # `applied: true` and exited 0 -- nothing written, surface saying otherwise.
        refused = [msg for st, msg in results if st == "refused"]
        wrote = [msg for st, msg in results if st == "written"]
        head = (
            "Nothing was written. Show the operator this, and register it only if they agree:\n"
            if dry
            else ""
        )
        c.out(
            head + "\n".join(f"  {x}" for x in actions),
            {
                "actions": actions,
                # What actually happened, per entry -- not one flag asserting it all
                # worked. `applied` is true only when something was really written.
                "applied": bool(wrote) and not dry,
                "written": len(wrote),
                "refused": refused,
            },
        )
        if refused:
            return FAIL
        return OK

    pipeline = list(c.cfg.gates.task_pipeline)
    cover = CO.gate_coverage(c.repo, statuses, pipeline)
    payload = {
        "companions": [
            {
                "id": st.companion.id,
                "title": st.companion.title,
                "gates": st.companion.gates,
                "state": st.state,
                "registered_in": st.registered_in,
                "detail": st.detail,
                "install": st.companion.install,
                "url": st.companion.url,
                "default": st.companion.default,
                "kind": st.companion.kind,
            }
            for st in statuses
        ],
        "gate_coverage": cover,
        "uncovered_gates": [g for g, ids in cover.items() if not ids],
    }
    # "Registered" is not a state a `cli` companion can reach -- there is nothing to
    # register. Judging one by it would make `companions` exit 2 forever the moment a
    # cli entry joined the registry, which trains the reader to ignore the exit code.
    # For those, INSTALLED is the goal state.
    gaps = [st for st in statuses if st.is_gap]
    if c.json:
        print(json.dumps(payload, indent=2))
        return NOTHING if gaps else OK

    lines = ["Companion tools", ""]
    for st in statuses:
        mark = {"registered": "[x]", "installed": "[+]", "missing": "[ ]", "unknown": "[?]"}[
            st.state
        ]
        tag = "" if st.companion.default else "  (opt-in)"
        if not st.companion.is_mcp and st.installed is True:
            mark = "[x]"
        lines.append(f"  {mark} {st.companion.id:<10s} {st.companion.title}{tag}")
        lines.append(f"       gates: {', '.join(st.companion.gates) or '—'}")
        if st.state == "registered":
            lines.append(f"       registered for: {', '.join(st.registered_in)}")
        elif st.state == "installed" and not st.companion.is_mcp:
            lines.append(
                f"       installed ({st.detail}). A {st.companion.kind} tool — the agent "
                f"shells out to it, so there is nothing to register."
            )
        elif st.state == "installed":
            lines.append(f"       installed ({st.detail}) but no agent is configured to launch it.")
            lines.append(f"       -> ddflow companions add --id {st.companion.id}")
        elif st.state == "unknown":
            lines.append(f"       not checked ({st.detail}) — re-run without --no-probe")
        else:
            lines.append(f"       not here: {st.detail}")
            lines.append(f"       -> ask the operator, then: {st.companion.install}")
            if st.companion.note:
                lines.append(f"          note: {st.companion.note}")
            if st.companion.is_mcp:
                lines.append(f"          then: ddflow companions add --id {st.companion.id}")
            if st.companion.url:
                lines.append(f"          {st.companion.url}")
        if st.companion.why:
            lines.append(f"       {st.companion.why.strip().splitlines()[0]}")
        lines.append("")
    uncovered = payload["uncovered_gates"]
    if uncovered:
        lines += [
            "Gates in this project's task pipeline with no companion behind them:",
            f"  {', '.join(uncovered)}",
            "  Not a failure — several of these are judgement an agent does directly.",
            "  It is the list to check when a gate has been passing suspiciously easily.",
            "",
        ]
    if gaps:
        # Addressed to the agent, because the agent is who reads this. ddflow does not
        # install anything itself -- running an install command on someone's machine is
        # the operator's decision -- but "here is a gap" without "here is what to do
        # about it" is a report nobody acts on.
        lines += [
            "WHAT TO DO ABOUT THIS (agent):",
            "  Tell the operator which of these are missing, what each one buys, and",
            "  what installing it would run. If they agree, run the install command",
            "  yourself and then `ddflow companions add --id <id>`. If they decline,",
            "  record the gates it serves as `unavailable` when you reach them — never",
            "  as passed on your own word.",
            "",
        ]
    print("\n".join(lines))
    return NOTHING if gaps else OK


#: How each event kind reads in a timeline. Absent kinds fall back to the kind name,
#: which is honest — a new event type shows up as itself rather than being silently
#: dropped from the history, which is the failure `replay` had with decisions.
def _help_topics() -> list[str]:
    """The topic names, read from the one place that defines them.

    Imported lazily and inside a function so `build_parser` does not drag the MCP tool
    table in through `help.grouped_tools`: the parser is built on EVERY invocation,
    including `ddflow next` in a hot loop.
    """
    from ..services.help import TOPICS

    return list(TOPICS)


def cmd_help(a, c: Ctx) -> int:
    """`ddflow help [topic]` — what this is, what it can do, what the workflow is.

    Not argparse's `--help`, which lists 43 subcommands alphabetically and explains
    neither what any of them is for nor which to reach for first. The pages are
    templates, so `ddflow prompts`-style overriding applies: a project can rewrite its
    own onboarding without a code change.
    """
    from ..services import help as H
    from ..services.prompts import TemplateError

    # The tool registry is handed IN: `services/` sits below `surfaces/`, so the help
    # renderer may not reach up for it. Function-local for the same reason `cmd_mcp`'s
    # is -- the parser is rebuilt on every invocation and must not drag it along.
    from .mcp import TOOLS

    try:
        text = H.render_topic(a.topic, c.repo) if a.topic else H.render_index(c.repo, tools=TOOLS)
    except TemplateError as e:
        print(str(e), file=sys.stderr)
        return FAIL
    c.out(text, {"topic": a.topic or "index", "text": text, "topics": H.TOPICS})
    return OK


def _import_verify(c: Ctx, st) -> int:
    """`ddflow import --verify` — status, still-true, and did-anyone-finish-it.

    Three exit codes because there are three answers and collapsing them loses the one
    that matters: `2` is "nothing was ever imported", which is not a failure and not a
    pass; `1` is "imported, and here is what a human still has to decide"; `0` is
    "imported and consistent".
    """
    from ..services import importer as IM

    r = IM.verify_import(c.repo, st)
    findings = r.findings
    payload = {
        "imported": r.imported,
        "total": r.total,
        "first_at": r.first_at,
        "last_at": r.last_at,
        "findings": findings,
        "tasks_without_globs": r.no_globs,
        "shipped_with_open_tasks": r.shipped_drift,
        "vanished_sources": [{"item": i, "source": src} for i, src in r.vanished],
        "empty_sources": r.empty_sources,
        "unstructured_provenance": r.unstructured,
        "branches_without_globs": r.no_globs_branches,
        # Separate from `verified` on purpose. "Nothing was imported" and "the import
        # is in good order" are different answers, and a single boolean collapses them
        # into the vacuous one: `verified: true` with `imported: {}` reads as checked-
        # and-fine to anything that does not also read the exit code -- and over MCP
        # exit 2 is not an error, so `_meta` is the only place the truth was.
        "imported_anything": bool(r.total or r.unstructured),
        "new_since_import": [
            {"kind": f.kind, "id": f.ident, "title": f.title, "source": f.source} for f in r.drift
        ],
        "notes": r.notes,
        "verified": bool(r.total) and not findings,
    }
    if c.json:
        print(json.dumps(payload, indent=2, default=str))
        return NOTHING if not r.total and not r.unstructured else (FAIL if findings else OK)

    if not r.total and not r.unstructured:
        print("Nothing in this queue was imported.")
        for n in r.notes:
            print(f"  {n}")
        return NOTHING

    when = f" between {r.first_at[:10]} and {r.last_at[:10]}" if r.first_at else ""
    lines = [f"Imported{when}:", ""]
    lines += [f"  {n:>6} {kind}(s)" for kind, n in sorted(r.imported.items())]
    if r.unstructured:
        lines.append(f"  {len(r.unstructured):>6} with prose-only provenance (older import)")
    lines.append("")
    if findings:
        lines.append("Left to decide or fix:")
        lines += [f"  - {f}" for f in findings]
    else:
        lines.append("Consistent: the queue matches the sources, and every imported")
        lines.append("task declares what it writes.")
    lines.append("")
    lines += [f"  {n}" for n in r.notes]
    lines.append("")
    lines.append(
        "`ddflow import` (no flags) shows what a re-run would add; the "
        "/import-existing-project prompt walks through the half that needs an operator."
    )
    print("\n".join(lines))
    return FAIL if findings else OK


def cmd_import(a, c: Ctx) -> int:
    """Propose what an existing project already has, so the queue starts where it is.

    Reads and reports by default; `--apply` writes. A project adopting ddflow on day
    400 has four hundred days of work, and a queue that starts empty tells an agent
    "nothing is in flight" about a repository with three branches in flight.

    Exit 2 when there is nothing to propose — "no data" reported as itself.
    """
    from ..services import importer as IM

    st = c.state()
    if a.verify:
        # `--include-done` and `--max-tasks` shape an IMPORT. Reading past them here
        # would be the silent-knob-drop shape, standing next to a flag that is loudly
        # refused two lines down.
        shaping = [
            f for f, on in (("--include-done", a.include_done), ("--max-tasks", a.max_tasks)) if on
        ]
        if shaping:
            print(
                f"--verify reports on the import that happened; {', '.join(shaping)} "
                f"shape(s) one that has not. Run them separately.",
                file=sys.stderr,
            )
            return FAIL
        if a.apply:
            # Refused rather than resolved. `--verify` READS and `--apply` WRITES, and
            # picking one silently is how an operator who asked to import ends up
            # having only looked -- or worse, the other way round.
            print(
                "--verify and --apply ask for different things: one reports on the "
                "import that happened, the other performs one. Run them separately.",
                file=sys.stderr,
            )
            return FAIL
        return _import_verify(c, st)
    # The flag overrides the knob; 0 means 'no flag given', so an operator who set
    # [importer] max_tasks in the config is not silently overruled by an argparse
    # default that looks like a choice and is not one.
    plan = IM.plan_import(
        c.repo,
        st,
        include_done=a.include_done,
        max_tasks=a.max_tasks or c.cfg.importer.max_tasks,
    )
    payload = {
        "summary": plan.summary(),
        "found": [
            {
                "kind": f.kind,
                "id": f.ident,
                "title": f.title,
                "source": f.source,
                "done": f.done,
                "needs": f.needs,
                "globs": f.globs,
            }
            for f in plan.found
        ],
        "skipped_existing": plan.skipped_existing,
        "empty_sources": plan.empty_sources,
        "notes": plan.notes,
        "applied": False,
    }

    if a.apply and plan.found:
        counts = IM.apply_import(c.repo, c.log, plan)
        payload["applied"] = True
        payload["written"] = counts
        c.out(
            "Imported: "
            + ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
            + "\n  Every item records where it came from. Review with `ddflow board`, "
            "then give each task its globs — an item with no declared globs is one the "
            "conflict detector cannot protect.",
            payload,
        )
        return OK

    if c.json:
        print(json.dumps(payload, indent=2, default=str))
        return OK if plan.found else NOTHING

    if not plan.found:
        print("Nothing to import.")
        for n in plan.notes:
            print(f"  {n}")
        return NOTHING

    lines = ["What this project already has (nothing written yet):", ""]
    preview = c.cfg.importer.preview_rows
    for kind in IM.KINDS:
        rows = plan.by_kind(kind)
        if not rows:
            continue
        lines.append(f"  {len(rows)} {kind}(s):")
        for f in rows[:preview]:
            mark = "[x]" if f.done else "[ ]"
            lines.append(f"    {mark} {f.ident:<28s} {f.title[:52]:<52s} {f.source}")
        if len(rows) > preview:
            lines.append(f"    ... and {len(rows) - preview} more")
        lines.append("")
    if plan.skipped_existing:
        lines.append(
            f"  {len(plan.skipped_existing)} already in the queue, left alone "
            f"(this command is safe to re-run)."
        )
        lines.append("")
    for n in plan.notes:
        lines.append(f"  NOTE: {n}")
    lines += [
        "",
        "  `ddflow import --apply` writes these. Before you do:",
        "    - the headings became phases and the checkboxes tasks, which is a GUESS;",
        "    - no task has globs unless the file declared them, and a task with no",
        "      globs is one two agents can collide on;",
        "    - dependencies are only what the file said.",
        "  The `/import-existing-project` workflow walks an agent through fixing those",
        "  WITH the operator, which is the half this command cannot do.",
    ]
    print("\n".join(lines))
    return OK


def cmd_adopt(a, c: Ctx) -> int:
    from ..services.adopt import AGENT_TARGETS, adopt

    agents = _csv(a.agents) or list(AGENT_TARGETS)
    try:
        actions = adopt(
            c.repo,
            agents,
            docs_dir=a.docs,
            install_hooks=c.cfg.enforce.install_hooks_on_setup,
            launch=a.launch,
            image=a.image,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return FAIL
    cmd_init(a, c)
    # Name the companion gap at adoption time. A project that adopts ddflow and stops
    # has a `standards` gate with nothing behind it and a `rules` gate reading no
    # memory -- and because an agent gate passes on an assertion, that gap is invisible
    # in exactly the way this design exists to prevent. Detection only; nothing is
    # installed, because fetching and running code on someone's machine is not a thing
    # a work-queue tool gets to do.
    # `is_gap`, so an installed command-line companion is not reported as "installed
    # here but not wired up" -- there is nothing to wire up, and the command offered
    # below would refuse it. Third of the four sites that each re-derived "goal state";
    # they all ask `Status` now.
    statuses = _scan_companions(c.repo)
    if statuses is None:
        return FAIL
    ready, absent = [], []
    for st in statuses:
        if not st.is_gap:
            continue
        (ready if st.state == "installed" else absent).append(st.companion.id)
    tail = ""
    if ready:
        tail += (
            f"\n\nInstalled here but not wired up: {', '.join(ready)}.\n"
            f"  ddflow companions add --agents {agents[0]}"
        )
    if absent:
        tail += f"\n\nNot installed: {', '.join(absent)} — `ddflow companions` has the commands."
    c.out(
        "\n".join(f"  {x}" for x in actions) + f"\n\nddflow adopted for: {', '.join(agents)}.\n"
        f"  1. set your test command in .ddflow/gates.toml\n"
        f"  2. ddflow phase add P1 --title '...'\n"
        f"  3. tell your agent: implement phase P1" + tail,
        {
            "actions": actions,
            "agents": agents,
            "companions_ready": ready,
            "companions_absent": absent,
        },
    )
    return OK


def _lessons_cadence(st, cfg) -> list[dict[str, Any]]:
    """Is a lessons-compression pass due?

    Measured as GROWTH since the last recorded pass, not as an absolute size. An
    absolute threshold fires forever once crossed -- including immediately after a pass
    that just correctly compressed the corpus -- which trains everyone to ignore it.
    Growth goes quiet when the work is done, which is the only behaviour that keeps a
    cadence trigger credible.

    Both halves must clear: total bytes AND bytes-per-entry. Dividing by entry count
    alone would fire on a MERGE (fewer entries, same bytes => per-entry rises), i.e. on
    exactly the action the cadence exists to produce.
    """
    live = [x for x in st.lessons.values() if not x.superseded_by]
    if len(live) < cfg.lessons.cadence_min_entries:
        return []
    runs = st.cadences.get("lessons_compression", [])
    now_bytes = sum(len(x.text().encode("utf-8")) for x in live)
    now_per = now_bytes / max(1, len(live))
    if not runs:
        return [
            {
                "cadence": "lessons_compression",
                "since": len(live),
                "every": 0,
                "unit": "entries (no baseline recorded yet)",
            }
        ]
    try:
        was = json.loads(runs[-1].get("result") or "{}")
        was_bytes, was_entries = float(was["bytes"]), int(was["entries"])
    except (ValueError, KeyError, TypeError):
        return []
    was_per = was_bytes / max(1, was_entries)
    grow_bytes = (now_bytes - was_bytes) / max(1.0, was_bytes) * 100
    grow_per = (now_per - was_per) / max(1e-9, was_per) * 100
    thresh = cfg.lessons.cadence_growth_pct
    if grow_bytes >= thresh and grow_per >= thresh:
        return [
            {
                "cadence": "lessons_compression",
                "since": round(grow_per, 1),
                "every": thresh,
                "unit": f"% growth per entry (total +{grow_bytes:.1f}%)",
            }
        ]
    return []


def cmd_mcp(a, c: Ctx) -> int:
    from ..surfaces.mcp import serve

    serve(c.repo)
    return OK


def _wrap(text: str, width: int) -> list[str]:
    import textwrap

    return textwrap.wrap(" ".join(text.split()), width)


def _starter_config() -> str:
    """The single configuration file.

    One file, not two. An earlier version also wrote `.ddflow/gates.toml` carrying a
    placeholder `unit_tests.command`, and because gates.toml wins over config.toml that
    placeholder silently overrode anything `ddflow configure` wrote -- so the documented
    way to set the test command could not set the test command. Splitting gates into
    their own file is still supported for operators who want it; it is just not the
    default, because a default that creates two sources of truth will produce two
    sources of truth.
    """
    return """# ddflow configuration — everything in one file.
# `ddflow config --explain` documents every knob. Only what you change needs to be
# here; everything else keeps its default.

# ---------------------------------------------------------------------------------
# THE ONE THING YOU MUST SET: how this project runs its tests.
# ---------------------------------------------------------------------------------
# [gate.unit_tests]
# command = "pytest -q"     # or "npm test" · "cargo test" · "go test ./..." · "make check"
#
# Set it with:   ddflow config --set gate.unit_tests.command "pytest -q"
# Until it is set, the unit_tests gate reports UNAVAILABLE — which is honest, and
# blocks completion, rather than passing vacuously.
#
# Left COMMENTED on purpose: an empty table here would collide with the block that
# `ddflow config --append-toml` writes, since TOML forbids a duplicate table, and the
# documented way to configure the project would fail on a fresh install.

# ---------------------------------------------------------------------------------
# A cross-family reviewer makes the `critic` gate real rather than self-reported.
# `ddflow reviewers detect --write` finds a local model server and fills this in.
# ---------------------------------------------------------------------------------
# [[reviewer]]
# name     = "local"
# base_url = "http://127.0.0.1:11434/v1"
# model    = "qwen3:8b"
# family   = "alibaba"            # must differ from the authoring model's family
# gates    = ["critic"]
# api_key_env = "MY_API_KEY"      # the NAME of an env var, never the key itself

[lease]
ttl_s = 1800            # how long a claim survives without a heartbeat
heartbeat_s = 300

[worktree]
enabled = true
max_parallel = 4

[schedule]
max_parallel_tasks = 4

[enforce]
# "block" makes the pre-commit hook REFUSE a commit touching paths no lease of yours
# covers — the only layer of this workflow that does not rely on the agent agreeing.
# Starts at "warn" so adopting ddflow never breaks an existing repo on day one.
commit_without_lease = "warn"

[session]
brief_max_tokens = 1200
"""


# -- parser ----------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ddflow",
        description="A portable work-queue kernel for AI coding agents. "
        "The event log is the source of truth; everything else is derived.",
    )
    p.add_argument(
        "--repo", help="repository root (default: cwd, resolved to the primary checkout)"
    )
    p.add_argument("--agent", help="agent identity (default: host-pid). Shards the log.")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    s = p.add_subparsers(dest="cmd", required=True)

    s.add_parser("init", help="create .ddflow/ in this repository").set_defaults(fn=cmd_init)

    # A phase and a task differ by two arguments. Writing both blocks out by hand is
    # how `--priority` ended up on one and not the other twice before, and how the
    # `record`/`skip` pair right below this was loop-generated for the same reason.
    def _item_parser(sub, name: str, help_text: str, fn):
        grp = s.add_parser(name, help=help_text)
        sub_p = grp.add_subparsers(dest=f"{name}_cmd", required=True)
        add = sub_p.add_parser("add")
        add.add_argument("id")
        add.add_argument("--title", default="")
        add.add_argument("--needs")
        add.add_argument("--globs")
        add.add_argument("--tags")
        add.add_argument("--body")
        add.add_argument("--priority", type=int, default=A_ITEMS.DEFAULT_PRIORITY)
        add.set_defaults(fn=fn)
        return add

    _item_parser(s, "phase", "add a phase", cmd_phase_add)
    tad = _item_parser(s, "task", "add a task", cmd_task_add)
    tad.add_argument("--phase", default="", help="owning phase")
    tad.add_argument(
        "--parent",
        default="",
        help="owning phase OR task — a task parent makes this a SUB-TASK, which "
        "carries its own globs and dependencies like any other task",
    )

    sp = s.add_parser(
        "split", help="split an item into sub-tasks in place, keeping its id and history"
    )
    sp.add_argument("id")
    sp.add_argument(
        "--into", action="append", default=[], help="repeatable: 'sub-id=title', or just 'sub-id'"
    )
    sp.add_argument(
        "--globs", default="", help="globs for the children (default: inherit the parent's)"
    )
    sp.add_argument("--needs", default="", help="dependencies for the FIRST child")
    sp.set_defaults(fn=cmd_split)

    up = s.add_parser("update", help="change an item's fields")
    up.add_argument("id")
    for f in ("title", "body", "needs", "globs", "tags"):
        up.add_argument(f"--{f}")
    up.add_argument("--priority", type=int)
    up.set_defaults(fn=cmd_item_update)

    nx = s.add_parser("next", help="what may start now (exit 2 = nothing actionable)")
    nx.add_argument("--phase", default="")
    nx.add_argument("--kind", default=A_LIFECYCLE.DEFAULT_NEXT_KIND, choices=["task", "phase"])
    nx.set_defaults(fn=cmd_next)

    cl = s.add_parser("claim", help="lease an item + create its worktree (exit 3 = refused)")
    cl.add_argument("id")
    cl.add_argument("--globs")
    cl.add_argument("--note")
    cl.add_argument("--force", action="store_true")
    cl.add_argument("--no-worktree", action="store_true")
    cl.set_defaults(fn=cmd_claim)

    hb = s.add_parser("heartbeat", help="renew a lease")
    hb.add_argument("id")
    hb.set_defaults(fn=cmd_heartbeat)
    rl = s.add_parser("release", help="give up a lease")
    rl.add_argument("id")
    rl.add_argument("--note")
    rl.set_defaults(fn=cmd_release)

    ap = s.add_parser(
        "approve",
        help="a PERSON clears (or rejects) a human-approval gate — no MCP equivalent",
    )
    ap.add_argument("id")
    ap.add_argument("gate")
    ap.add_argument("--note", help="what you looked at, for the record")
    ap.add_argument("--reject", action="store_true", help="refuse it; --reason required")
    ap.add_argument("--reason", help="why it was rejected — a 'no' nobody can act on is a stall")
    ap.set_defaults(fn=cmd_approve)

    g = s.add_parser("gate", help="run / record / inspect a gate")
    g_s = g.add_subparsers(dest="gate_cmd", required=True)
    gst = g_s.add_parser("status")
    gst.add_argument("id")
    gst.set_defaults(fn=cmd_gate, gate="")
    grun = g_s.add_parser("run")
    grun.add_argument("id")
    grun.add_argument("gate")
    grun.set_defaults(fn=cmd_gate)
    gvf = g_s.add_parser(
        "verify",
        help="break what this gate guards and require it to notice (exit 1 = it cannot)",
    )
    gvf.add_argument("id")
    gvf.add_argument("gate")
    gvf.set_defaults(fn=cmd_gate)
    for name in ("record", "skip"):
        gr = g_s.add_parser(name)
        gr.add_argument("id")
        gr.add_argument("gate")
        gr.add_argument("--outcome", default="passed", choices=list(GATE_OUTCOMES))
        gr.add_argument("--reason", default="")
        gr.add_argument("--evidence", default="")
        gr.add_argument("--command", default="")
        gr.add_argument("--exit-code", type=int)
        gr.add_argument("--model", default="", help="reviewer model, for family independence")
        gr.add_argument("--output-file", default="")
        gr.set_defaults(fn=cmd_gate)

    cp = s.add_parser("complete", help="finish an item (exit 3 = gates not satisfied)")
    cp.add_argument("id")
    cp.add_argument("--sha", default="")
    cp.add_argument("--model", default="", help="the AUTHOR's model, for independence check")
    cp.add_argument("--force", action="store_true")
    cp.set_defaults(fn=cmd_complete)

    ab = s.add_parser("abandon", help="stop work on an item without completing it")
    ab.add_argument("id")
    ab.add_argument("--reason", required=True)
    ab.add_argument("--force", action="store_true")
    ab.set_defaults(fn=cmd_abandon)

    rm = s.add_parser("remove", help="take an item out of the queue (recorded, not erased)")
    rm.add_argument("id")
    rm.add_argument("--reason", default="")
    rm.add_argument("--force", action="store_true")
    rm.set_defaults(fn=cmd_remove)

    bl = s.add_parser("block")
    bl.add_argument("id")
    bl.add_argument("--reason", required=True)
    bl.set_defaults(fn=cmd_block)

    mg = s.add_parser("merge", help="merge an item's branch from the primary checkout")
    mg.add_argument("id")
    mg.add_argument("--message", default="")
    mg.add_argument("--keep", action="store_true")
    mg.add_argument("--allow-dirty", action="store_true")
    mg.set_defaults(fn=cmd_merge)

    br = s.add_parser("brief", help="budgeted session-start pack")
    br.add_argument("--item", default="")
    br.add_argument("--phase", default="")
    br.add_argument(
        "--check-recovery", action="store_true", default=A_LIFECYCLE.DEFAULT_CHECK_RECOVERY
    )
    br.set_defaults(fn=cmd_brief)

    ls = s.add_parser("lesson")
    ls_s = ls.add_subparsers(dest="lesson_cmd", required=True)
    la = ls_s.add_parser("add")
    la.add_argument("--id", default="")
    la.add_argument("--title", required=True)
    la.add_argument("--rule", default="")
    la.add_argument("--why", default="")
    la.add_argument("--how", default="")
    la.add_argument("--tags", default="")
    la.add_argument("--seen-in", default="")
    la.add_argument("--supersedes", default="")
    la.set_defaults(fn=cmd_lesson)
    lse = ls_s.add_parser("search")
    lse.add_argument("query")
    lse.add_argument("--limit", type=int, default=None, help="default: [lessons].max_results")
    lse.set_defaults(fn=cmd_lesson)

    rc = s.add_parser(
        "recall",
        help="'have we been here before?' — search decisions, lessons, research, bugs, "
        "tasks and past prompts at once",
    )
    rc.add_argument("query")
    rc.add_argument("--limit", type=int, default=3, help="hits per source")
    rc.add_argument(
        "--sources",
        default="",
        help="comma-separated subset: decisions,lessons,research,bugs,items,prompts",
    )
    rc.add_argument("--max-chars", type=int, default=4000)
    rc.set_defaults(fn=cmd_recall)

    dc = s.add_parser("decision", help="architectural decisions: record and consult")
    dc_s = dc.add_subparsers(dest="decision_cmd", required=False)
    dca = dc_s.add_parser("add")
    dca.add_argument("--id", default="")
    dca.add_argument("--title", required=True)
    dca.add_argument("--decision", required=True, help="what was DECIDED (not what was discussed)")
    dca.add_argument("--context", default="", help="the forces: why a decision was needed")
    dca.add_argument("--consequences", default="", help="what it costs, incl. what it makes harder")
    dca.add_argument("--alternatives", default="", help="what was rejected, and why")
    dca.add_argument(
        "--globs",
        default="",
        help="the code this governs; without it the decision can only be found by search",
    )
    dca.add_argument("--tags", default="")
    dca.add_argument(
        "--sources",
        default="",
        help="where this came from: an ADR path, a URL, a commit sha (comma-separated)",
    )
    dca.add_argument("--item", default="")
    dca.add_argument("--by", default="", help="operator | agent | a name")
    dca.add_argument("--status", default="accepted", choices=["proposed", "accepted", "superseded"])
    dca.add_argument("--supersedes", default="")
    dca.set_defaults(fn=cmd_decision)
    dcl = dc_s.add_parser("list")
    dcl.add_argument("--all", action="store_true")
    dcl.set_defaults(fn=cmd_decision)
    dcs = dc_s.add_parser("show")
    dcs.add_argument("id")
    dcs.set_defaults(fn=cmd_decision)
    dcf = dc_s.add_parser("search")
    dcf.add_argument("query")
    dcf.add_argument("--limit", type=int, default=5)
    dcf.set_defaults(fn=cmd_decision)
    dcap = dc_s.add_parser("applicable", help="decisions governing an item's declared files")
    dcap.add_argument("id")
    dcap.set_defaults(fn=cmd_decision)
    dcsu = dc_s.add_parser("supersede")
    dcsu.add_argument("id")
    dcsu.add_argument("--by", required=True, help="the decision that replaces it")
    dcsu.add_argument("--reason", default="")
    dcsu.set_defaults(fn=cmd_decision)
    dc.set_defaults(fn=cmd_decision, decision_cmd="list", all=False)

    stt = s.add_parser("status", help="one answer to 'what is the state of this project?'")
    stt.set_defaults(fn=cmd_status)

    rs = s.add_parser("research")
    rs.add_argument("--id", default="")
    rs.add_argument("--question", required=True)
    rs.add_argument("--claim", default="")
    rs.add_argument("--mechanism", default="")
    rs.add_argument("--falsifier", default="")
    rs.add_argument("--probe", default="")
    rs.add_argument("--probe-output", default="")
    rs.add_argument("--verdict", required=True)
    rs.add_argument("--sources", default="")
    rs.add_argument("--budget", default="")
    rs.add_argument("--item", default="")
    rs.set_defaults(fn=cmd_research)

    bg = s.add_parser("bug")
    bg_s = bg.add_subparsers(dest="bug_cmd", required=True)
    bf = bg_s.add_parser("found")
    bf.add_argument("--id", default="")
    bf.add_argument("--summary", required=True)
    bf.add_argument("--item", default="")
    bf.set_defaults(fn=cmd_bug)
    bx = bg_s.add_parser("fixed")
    bx.add_argument("id")
    bx.add_argument("--regression-test", default="")
    bx.add_argument("--lesson", default="")
    bx.add_argument("--lesson-title", default="")
    bx.add_argument("--lesson-rule", default="")
    bx.set_defaults(fn=cmd_bug)

    se = s.add_parser("session")
    se_s = se.add_subparsers(dest="session_cmd", required=True)
    ss = se_s.add_parser("start")
    ss.add_argument("--model", default="")
    ss.add_argument("--tool", default="")
    ss.set_defaults(fn=cmd_session)
    sp = se_s.add_parser("prompt")
    sp.add_argument("session")
    sp.add_argument("--text")
    sp.add_argument("--item", default="")
    sp.set_defaults(fn=cmd_session)
    sn = se_s.add_parser("note")
    sn.add_argument("session")
    sn.add_argument("--text")
    sn.add_argument("--item", default="")
    sn.set_defaults(fn=cmd_session)
    sd = se_s.add_parser("end")
    sd.add_argument("session")
    sd.add_argument("--summary", default="")
    sd.set_defaults(fn=cmd_session)

    rp = s.add_parser("replay", help="reconstruct the decision history from the log")
    rp.add_argument("--out", default="")
    rp.add_argument("--verify", action="store_true")
    rp.set_defaults(fn=cmd_replay)

    rc = s.add_parser("recover", help="find crashed agents' work (exit 2 = nothing)")
    rc.add_argument("--item", default="")
    rc.add_argument("--apply", action="store_true")
    rc.set_defaults(fn=cmd_recover)

    pg = s.add_parser("progress", help="work actually done, aggregated from the log")
    pg.add_argument("id", nargs="?", default="")
    pg.set_defaults(fn=cmd_progress)

    lp = s.add_parser("loops", help="circular references and runtime loops (exit 2 = none)")
    lp.set_defaults(fn=cmd_loops)

    cu = s.add_parser(
        "cleanup", help="classify ddflow worktrees/branches; --apply lands the safe ones"
    )
    cu.add_argument("--apply", action="store_true")
    cu.set_defaults(fn=cmd_cleanup)

    s.add_parser("doctor", help="integrity + health check").set_defaults(fn=cmd_doctor)
    s.add_parser("rebuild", help="re-derive the index from the log").set_defaults(fn=cmd_rebuild)

    rn = s.add_parser("render", help="regenerate the human-readable views")
    rn.add_argument("--out", default=A_REPORTING.DEFAULT_RENDER_DIR)
    rn.add_argument(
        "--show",
        default="",
        help="print ONE view to stdout instead of writing files: lessons, research, board",
    )
    rn.set_defaults(fn=cmd_render)
    bd = s.add_parser("board")
    bd.add_argument("--phase", default="")
    bd.set_defaults(fn=cmd_board)
    sh = s.add_parser("show")
    sh.add_argument("id")
    sh.set_defaults(fn=cmd_show)

    cf = s.add_parser("config", help="print every knob, its value and its source")
    cf.add_argument("--explain", action="store_true")
    cf.add_argument("--filter", default="")
    cf.add_argument(
        "--set",
        default="",
        help="edit one key in place, e.g. --set gate.unit_tests.command 'pytest -q'",
    )
    cf.add_argument("value", nargs="?", default="", help="the value, when --set is used")
    cf.add_argument(
        "--append-toml",
        default="",
        help="append this TOML to .ddflow/config.toml (validated first)",
    )
    cf.set_defaults(fn=cmd_config)

    cd = s.add_parser("cadence", help="which periodic passes are due (exit 2 = none)")
    cd.add_argument("--ran", default="")
    cd.add_argument("--note", default="")
    cd.set_defaults(fn=cmd_cadence)

    rv = s.add_parser("reviewers", help="find, list and test cross-family reviewers")
    rv_s = rv.add_subparsers(dest="reviewers_cmd", required=True)
    rvd = rv_s.add_parser("detect", help="probe well-known local endpoints")
    rvd.add_argument(
        "--write",
        action="store_true",
        help="append the discovered reviewers to .ddflow/config.toml",
    )
    rvd.set_defaults(fn=cmd_reviewers)
    rv_s.add_parser("list").set_defaults(fn=cmd_reviewers)
    rv_s.add_parser("presets", help="ready-made provider settings").set_defaults(fn=cmd_reviewers)
    rva = rv_s.add_parser("add", help="add a reviewer from a preset")
    rva.add_argument("--preset", default="", help="see `ddflow reviewers presets`")
    rva.add_argument("--name", default="")
    rva.add_argument("--model", default="")
    rva.add_argument("--base-url", default="")
    rva.add_argument("--gates", default="")
    rva.add_argument(
        "--no-launch",
        action="store_true",
        help="do not auto-start a local server for this reviewer",
    )
    rva.set_defaults(fn=cmd_reviewers)
    rvt = rv_s.add_parser("test", help="send a tiny known-buggy diff and check the reply")
    rvt.add_argument("name", nargs="?", default="")
    rvt.set_defaults(fn=cmd_reviewers)

    rw = s.add_parser("review", help="run the configured reviewer(s) over an item's diff")
    rw.add_argument("id", nargs="?", default="")
    rw.add_argument("--gate", default="critic")
    rw.add_argument(
        "--intent",
        default="",
        help="what the change is meant to do (defaults to the item's title/body)",
    )
    rw.add_argument("--context", default="")
    rw.add_argument("--base", default="")
    rw.set_defaults(fn=cmd_review)

    ad = s.add_parser("adopt", help="install ddflow into this project for one or more agents")
    ad.add_argument(
        "--agents",
        default="",
        help="comma-separated: claude,gemini,codex,copilot,kilo,cursor (default: all)",
    )
    ad.add_argument("--docs", default="docs/ddflow", help="where to write the drivers")
    ad.add_argument(
        "--launch",
        default="auto",
        choices=["auto", "uvx", "docker", "python"],
        help="how agents spawn the MCP server: 'auto' prefers uvx; 'docker' needs no "
        "Python toolchain at all",
    )
    ad.add_argument(
        "--image",
        default="ghcr.io/OWNER/ddflow:latest",
        help="container image used by --launch docker",
    )
    ad.set_defaults(fn=cmd_adopt)

    hi = s.add_parser("history", help="one timeline of everything that happened (exit 2 = nothing)")
    hi.add_argument("--item", default="", help="restrict to one item")
    hi.add_argument(
        "--kind",
        default="",
        help="comma-separated event kinds or families: 'gate', 'lease.acquired', 'decision,bug'",
    )
    hi.add_argument("--since", default="", help="ISO timestamp lower bound")
    hi.add_argument("--limit", type=int, default=40)
    hi.set_defaults(fn=cmd_history)

    wf = s.add_parser(
        "workflow",
        help="the rules this project runs by, and how to change them (exit 1 = incoherent)",
    )
    wfs = wf.add_subparsers(dest="workflow_cmd")
    wf.set_defaults(fn=cmd_workflow, dry_run=False)

    wfp = wfs.add_parser("pipeline", help="set the gates a task or phase passes through")
    wfp.add_argument("which", choices=["task", "phase"])
    wfp.add_argument("gates", help="comma-separated gate ids, in order")
    wfp.add_argument("--dry-run", action="store_true", help="show it; write nothing")
    wfp.set_defaults(fn=cmd_workflow)

    wfg = wfs.add_parser("gate", help="define or change one gate")
    wfg.add_argument("id")
    wfg.add_argument("--command", default="", help="what to run; makes it a command gate")
    wfg.add_argument("--prompt", default="", help="what to ask an agent; makes it an agent gate")
    wfg.add_argument("--title", default="")
    wfg.add_argument("--cwd", default="", choices=["", "worktree", "repo"])
    wfg.add_argument(
        "--reviewer",
        default="",
        choices=["", "different_family", "same_family_ok"],
        help="require a reviewer, and whether it must be a different model family",
    )
    wfg.add_argument("--timeout", type=int, default=0, help="seconds before it is unavailable")
    wfg.add_argument("--applies-to", default="", choices=["", "task", "phase", "both"])
    wfg.add_argument(
        "--into", default="", choices=["", "task", "phase", "both"], help="add to a pipeline"
    )
    wfg.add_argument("--after", default="", help="place it after this gate (default: last)")
    wfg.add_argument("--required", action="store_true", help="an item cannot complete without it")
    wfg.add_argument("--dry-run", action="store_true", help="show it; write nothing")
    wfg.set_defaults(fn=cmd_workflow)

    wfd = wfs.add_parser("drop", help="take a gate out of the pipelines (its definition stays)")
    wfd.add_argument("id")
    wfd.add_argument("--dry-run", action="store_true", help="show it; write nothing")
    wfd.set_defaults(fn=cmd_workflow)

    hp = s.add_parser(
        "help",
        help="what ddflow is, what it can do, and the workflow (try: ddflow help workflow)",
    )
    hp.add_argument(
        "topic",
        nargs="?",
        default="",
        help="one of: " + ", ".join(sorted(_help_topics())) + ". Omit for the overview.",
    )
    hp.set_defaults(fn=cmd_help)

    im = s.add_parser(
        "import",
        help="propose the existing project's work, lessons and decisions (exit 2 = nothing)",
    )
    im.add_argument("--apply", action="store_true", help="write them; default is a dry run")
    im.add_argument(
        "--verify",
        action="store_true",
        help="report what was already imported and whether it is still true: source "
        "drift, vanished source files, and the globs and decisions the import left for "
        "a human (exit 1 = findings, 2 = nothing imported)",
    )
    im.add_argument(
        "--include-done",
        action="store_true",
        help="also import already-ticked items, as completed. Off by default: a "
        "finished history is not a queue.",
    )
    im.add_argument(
        "--max-tasks",
        type=int,
        default=0,
        help="refuse to propose more tasks than this. 0 (the default) uses "
        "[importer] max_tasks from the config, which ships at 200. A bigger number is "
        "usually a whole history rather than a queue.",
    )
    im.set_defaults(fn=cmd_import)

    co = s.add_parser(
        "companions",
        help="companion MCP servers that serve the gates (exit 2 = a default one is missing)",
    )
    co_s = co.add_subparsers(dest="companions_cmd")
    co_list = co_s.add_parser("list", help="what is known, installed and registered")
    co_list.add_argument("--no-probe", action="store_true", help="skip the detection probes")
    co_add = co_s.add_parser("add", help="register installed companions in an agent's MCP config")
    co_add.add_argument("--id", default="", help="comma-separated; default: every installed one")
    co_add.add_argument("--agents", default="", help="comma-separated (default: claude)")
    co_add.add_argument(
        "--dry-run",
        action="store_true",
        help="show the exact config entry that would be written, and write nothing",
    )
    co_add.add_argument("--force", action="store_true", help="register one that is not installed")
    co_add.add_argument("--no-probe", action="store_true", help=argparse.SUPPRESS)
    co.set_defaults(fn=cmd_companions, companions_cmd="list", no_probe=False)
    co_list.set_defaults(fn=cmd_companions)
    co_add.set_defaults(fn=cmd_companions)

    pr = s.add_parser("prompts", help="inspect and override the prompt templates")
    pr_s = pr.add_subparsers(dest="prompts_cmd", required=True)
    pr_s.add_parser("list").set_defaults(fn=cmd_prompts)
    prs = pr_s.add_parser("show")
    prs.add_argument("name")
    prs.set_defaults(fn=cmd_prompts)
    pre = pr_s.add_parser("eject", help="copy the shipped templates into .ddflow/prompts/")
    pre.add_argument("name", nargs="?", default="")
    pre.add_argument("--force", action="store_true")
    pre.set_defaults(fn=cmd_prompts)

    hk = s.add_parser("hooks", help="install/inspect the enforcement git hook")
    hk_s = hk.add_subparsers(dest="hooks_cmd", required=True)
    hki = hk_s.add_parser("install")
    hki.add_argument(
        "--force",
        action="store_true",
        help="replace an existing pre-commit hook ddflow does not manage",
    )
    hki.set_defaults(fn=cmd_hooks)
    hk_s.add_parser("uninstall").set_defaults(fn=cmd_hooks)
    hk_s.add_parser("status").set_defaults(fn=cmd_hooks)
    hk_s.add_parser("check-commit", help="(invoked by the hook)").set_defaults(fn=cmd_hooks)

    s.add_parser("mcp", help="run the MCP stdio server over this repository").set_defaults(
        fn=cmd_mcp
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        ctx = Ctx(args)
        return int(args.fn(args, ctx))
    except KeyboardInterrupt:
        return 130
    except L.LeaseError as exc:
        print(str(exc), file=sys.stderr)
        return REFUSED
    except (W.GitError, ValueError, KeyError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return FAIL


if __name__ == "__main__":
    raise SystemExit(main())

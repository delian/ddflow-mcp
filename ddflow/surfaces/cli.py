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
import os
import sys

from ..api import items as A_ITEMS
from ..api import lifecycle as A_LIFECYCLE
from ..api import reporting as A_REPORTING
from ..core.events import SkewRefused
from ..core.model import GATE_OUTCOMES, fold
from ..infra import worktree as W
from ..services import gates as G
from ..services import leases as L
from ..services.adopt import AGENT_TARGETS
from . import dedupe_flags
from .commands.bisect import add_bisect_parser
from .commands.bug_reopen import add_bug_reopen_parser
from .commands.ci import add_ci_parser
from .commands.config import (  # noqa: F401  -- moved out of this module
    _config_set,
    _workflow_problems,
    _write_config,
)
from .commands.decisions import cmd_decision
from .commands.export import add_export_parser
from .commands.flow import cmd_flow, cmd_pr, cmd_promote, cmd_version
from .commands.gates import cmd_gate
from .commands.knowledge import (
    cmd_bug,
    cmd_history,
    cmd_job,
    cmd_lesson,
    cmd_memory,
    cmd_recall,
    cmd_research,
    cmd_session,
    cmd_similar,
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
    cmd_unblock,
    cmd_wait,
)
from .commands.operations import (
    cmd_cadence,
    cmd_cleanup,
    cmd_external,
    cmd_import,
    cmd_pins,
    cmd_precommit,
    cmd_tests,
)
from .commands.queue import cmd_phase_add, cmd_resolve, cmd_split, cmd_task_add
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
from .commands.rules import cmd_rule
from .commands.setup import (
    cmd_adopt,
    cmd_companions,
    cmd_config,
    cmd_help,
    cmd_hooks,
    cmd_init,
    cmd_prompts,
    help_topics,
)
from .commands.verify import add_verify_parser
from .commands.viewers_lists import register as register_list_viewers
from .commands.viewers_sessions import add_session_view_parsers
from .commands.workflow import cmd_workflow
from .context import (
    FAIL,
    NOTHING,
    OK,
    REFUSED,
    Ctx,
    _csv,
    _require_item,
)

#: Help for every `--globs` that takes paths to claim, add or update.
GLOBS_HELP = (
    "path globs, comma-separated (a.py,src/**) or a JSON array; repeat the flag to add "
    "more -- every value is kept"
)


class _Globs(argparse.Action):
    """A repeatable `--globs`: every value is KEPT, as a list, for `globspec.parse`.

    A plain store kept only the last value, so an agent that passed the flag ten times
    held one path -- or none -- and the conflict check never saw the rest (Bdc85898c40).
    """

    def __call__(self, parser, namespace, values, option_string=None):
        cur = getattr(namespace, self.dest, None)
        setattr(namespace, self.dest, [*(cur if isinstance(cur, list) else []), values])


def cmd_item_update(a, c: Ctx) -> int:
    # Through the api, like `ddflow_update` -- this wrote the event itself, so a field
    # the api validates (a release line that does not exist) went unchecked here.
    out = A_ITEMS.update(
        c.repo,
        a.id,
        A_ITEMS.ItemEdit(
            title=a.title,
            body=a.body,
            needs=None if a.needs is None else _csv(a.needs),
            # The raw values: the api reads them once. Parsed here as well, a JSON
            # array's element holding a comma was split by the second read (roborev).
            globs=a.globs,
            tags=None if a.tags is None else _csv(a.tags),
            priority=a.priority,
            line=a.line,
            resources=None if a.resources is None else _csv(a.resources),
            # Relative to where the caller stands, like any path typed in a shell.
            # An empty value stays empty, for the api to refuse: abspath("") is the cwd.
            worktree=a.worktree and os.path.abspath(a.worktree),
        ),
        agent=c.requested_agent,
    )
    if out.exit != OK:
        print(out.reason, file=sys.stderr)
        return out.exit
    msg = f"{a.id} updated: {', '.join(out.data['changed'])}"
    if out.data.get("branch"):
        msg += f"\n  now bound to {out.data['worktree']} on {out.data['branch']}"
    if "globs" in out.data["fields"]:
        # `--globs` REPLACES the list. Say what that took away: an agent widening its
        # claim with only the new paths otherwise drops the old ones from every
        # conflict check without a word (Bd8038b08a1).
        msg += f"\n  globs now: {', '.join(out.data['fields']['globs']) or '(none)'}"
        if out.data["globs_dropped"]:
            msg += (
                f"\n  dropped:   {', '.join(out.data['globs_dropped'])}  (--globs replaces "
                f"the list; pass every glob, old and new, to keep them)"
            )
    c.out(msg, out.body(("id", "changed", "globs_dropped")))
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


#: How each event kind reads in a timeline. Absent kinds fall back to the kind name,
#: which is honest — a new event type shows up as itself rather than being silently
#: dropped from the history, which is the failure `replay` had with decisions.
def cmd_mcp(a, c: Ctx) -> int:
    from ..surfaces.mcp import serve

    # The caller's own start (--repo, DDFLOW_REPO or cwd), as ddflow-mcp passes it: without it every
    # called_from-aware tool treats the caller as standing in the primary (Bc1fe69741f).
    serve(c.repo, called_from=c._start)
    return OK


class _PrintVersion(argparse.Action):
    """`ddflow --version`. Reads `SERVER_INFO`, what the server reports at handshake; it is
    built from `ddflow.__version__`, the one declared version, so the two cannot differ."""

    def __call__(self, parser, namespace, values, option_string=None):
        from .mcp import SERVER_INFO

        print(f"ddflow {SERVER_INFO['version']}")
        parser.exit()


def _accept_global_options_anywhere(root: argparse.ArgumentParser) -> None:
    """`--repo`, `--agent` and `--json` after the subcommand as well as before it.

    They lived on the root parser only, so `ddflow brief --agent X` failed "unrecognized
    arguments" while `ddflow --agent X brief` worked -- and agents append flags, told by
    every driver to "pass --agent on every call" without being told where (B9811d8617c).
    Copied onto every subparser, nested ones included, with `default=SUPPRESS`: given
    after the subcommand it sets the same attribute (and wins over one given before),
    and absent there it leaves the root's value alone instead of resetting it.
    """
    # Derived from the root, so a global option added there later is accepted after the
    # subcommand too, instead of drifting from a second hand-kept list.
    mirrored = []
    for action in root._actions:
        if not action.option_strings or isinstance(
            action, (argparse._HelpAction, argparse._SubParsersAction, _PrintVersion)
        ):
            continue
        kwargs: dict = {"dest": action.dest, "default": argparse.SUPPRESS}
        if isinstance(action, argparse._StoreTrueAction):
            kwargs["action"] = "store_true"
        elif type(action) is not argparse._StoreAction:
            raise TypeError(f"global option {action.option_strings} cannot be mirrored")
        mirrored.append((action.option_strings, kwargs))
    seen: set[int] = set()

    def walk(parser: argparse.ArgumentParser) -> None:
        for action in parser._actions:
            if not isinstance(action, argparse._SubParsersAction):
                continue
            for sub in action.choices.values():
                if id(sub) in seen:  # an alias is the same parser under another name
                    continue
                seen.add(id(sub))
                for flags, kwargs in mirrored:
                    if not set(flags) & set(sub._option_string_actions):
                        sub.add_argument(*flags, help=argparse.SUPPRESS, **kwargs)
                walk(sub)

    walk(root)


def _positive_int(v: str) -> int:
    n = int(v)
    if n < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return n


def build_parser() -> argparse.ArgumentParser:  # noqa: PLR0915
    # PLR0915 (statement count), suppressed HERE rather than for the whole file. argparse
    # construction is inherently one long sequence of near-identical statements, and
    # splitting it into a dozen `_add_x_parser` helpers would move the length without
    # reducing what actually matters: how much you must read to know what the CLI accepts.
    #
    # The file-wide ignore this replaces also covered C901 and PLR0912 for ~45 other
    # functions, three of which were over the limit and nobody knew (B163).
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
    p.add_argument(
        "--allow-older-version",
        action="store_true",
        dest="allow_older",
        help="let THIS session write although this ddflow is older than the one that last "
        "worked on the log (the skew guard); needs --reason, is recorded, and only the "
        "operator's insistence justifies it",
    )
    p.add_argument(
        "--reason",
        dest="skew_reason",
        default="",
        help="with --allow-older-version: why (a command's own --reason is used if it has one)",
    )
    # There was no such flag at all, and CI's build step -- `python -m ddflow --version`
    # against the built wheel -- failed with "the following arguments are required: cmd".
    # An Action rather than argparse's `version=` string: it runs, and exits, before the
    # required subcommand is checked, and it imports the version only when asked -- the
    # parser is built on every invocation and must not drag the MCP module in.
    p.add_argument("--version", action=_PrintVersion, nargs=0, help="print the version and exit")
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
        add.add_argument("--globs", action=_Globs, help=GLOBS_HELP)
        add.add_argument("--tags")
        add.add_argument("--body")
        add.add_argument("--priority", type=int, default=A_ITEMS.DEFAULT_PRIORITY)
        add.add_argument("--line", default="", help="release line (default: the current one)")
        add.add_argument(
            "--readd",
            action="store_true",
            help="file a REMOVED item's id again. An id still in the queue is always "
            "refused: change it with `ddflow update`",
        )
        dedupe_flags.add_flags(add)
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
    tad.add_argument(
        "--lines",
        default="",
        help="a FIX for several release lines, e.g. 1,2,3: written where [flow].port_strategy "
        "says, with a port task <id>@<line> generated for each other line",
    )
    tad.add_argument(
        "--port-of",
        default="",
        help="a FOLLOW-UP to an earlier fix: takes the lines that fix reached, so its own "
        "ports carry what this one lands",
    )

    sp = s.add_parser(
        "split", help="split an item into sub-tasks in place, keeping its id and history"
    )
    sp.add_argument("id")
    sp.add_argument(
        "--into", action="append", default=[], help="repeatable: 'sub-id=title', or just 'sub-id'"
    )
    sp.add_argument(
        "--globs",
        action=_Globs,
        default="",
        help="globs for the children (default: inherit the parent's); " + GLOBS_HELP,
    )
    sp.add_argument("--needs", default="", help="dependencies for the FIRST child")
    sp.set_defaults(fn=cmd_split)

    rs = s.add_parser(
        "resolve",
        help="settle a CONTESTED item (rival adds or claims from two clones), on the record",
    )
    rs.add_argument("id")
    rs.add_argument(
        "--keep",
        required=True,
        help="the definition's event id (or a 6+ character prefix), or the agent / lease "
        "holder, to keep — `ddflow show <id>` lists them",
    )
    rs.add_argument(
        "--refile-as",
        default="",
        help="re-add each definition NOT kept under these new ids (comma-separated, in "
        "`show` order) in the same transaction",
    )
    rs.set_defaults(fn=cmd_resolve)

    up = s.add_parser("update", help="change an item's fields")
    up.add_argument("id")
    for f in ("title", "body", "needs", "tags", "line", "resources"):
        up.add_argument(f"--{f}")
    up.add_argument(
        "--globs",
        action=_Globs,
        help="REPLACES the item's globs (and a claimed item's lease) with these; " + GLOBS_HELP,
    )
    up.add_argument("--priority", type=int)
    up.add_argument(
        "--worktree",
        help="rebind the item (and your live lease on it) to this linked worktree and the "
        "branch checked out there -- the way out of a binding to the wrong tree",
    )
    up.set_defaults(fn=cmd_item_update)

    nx = s.add_parser(
        "next",
        help="what may start now (exit 2 = nothing actionable, exit 1 = unknown --phase)",
    )
    nx.add_argument(
        "--phase", default="", help="limit to this phase; exit 1 if it names no phase or item"
    )
    nx.add_argument("--kind", default=A_LIFECYCLE.DEFAULT_NEXT_KIND, choices=["task", "phase"])
    nx.set_defaults(fn=cmd_next)

    cl = s.add_parser("claim", help="lease an item + create its worktree (exit 3 = refused)")
    cl.add_argument("id")
    cl.add_argument(
        "--globs",
        action=_Globs,
        help="what this claim writes (recorded on the item too); " + GLOBS_HELP,
    )
    cl.add_argument("--note")
    cl.add_argument("--force", action="store_true")
    cl.add_argument("--no-worktree", action="store_true")
    cl.add_argument(
        "--resources",
        default="",
        help="the resources this claim reserves, e.g. 'gpu:2' (recorded on the item too)",
    )
    cl.set_defaults(fn=cmd_claim)

    hb = s.add_parser("heartbeat", help="renew a lease")
    hb.add_argument("id")
    hb.set_defaults(fn=cmd_heartbeat)
    rl = s.add_parser("release", help="give up a lease")
    rl.add_argument("id")
    rl.add_argument("--note")
    rl.set_defaults(fn=cmd_release)

    wt = s.add_parser(
        "wait",
        help="sleep until an item (or anything) can be claimed; exit 2 = deadline, or waiting "
        "cannot help",
    )
    wt.add_argument("--item", default="", help="the item to wait for (default: anything ready)")
    wt.add_argument("--phase", default="", help="with no --item: anything ready in this phase")
    wt.add_argument("--kind", default=A_LIFECYCLE.DEFAULT_NEXT_KIND, choices=["task", "phase"])
    wt.add_argument(
        "--globs",
        action=_Globs,
        help="with --item: the globs you will claim with, so READY means that claim will "
        "succeed; " + GLOBS_HELP,
    )
    # None = unset, so an explicit 0 ("just ask, do not sleep") is not taken as the default.
    wt.add_argument(
        "--timeout",
        type=float,
        default=None,
        help=f"seconds to wait (default {A_LIFECYCLE.DEFAULT_WAIT_TIMEOUT_S}; 0 asks without "
        f"waiting)",
    )
    wt.add_argument("--poll", type=float, default=None, help="seconds between log checks")
    wt.set_defaults(fn=cmd_wait)

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
        gr.add_argument(
            "--outcome",
            default="passed",
            choices=list(GATE_OUTCOMES),
            help="failed, unavailable, partial and skipped each require --reason",
        )
        gr.add_argument(
            "--reason",
            default="",
            help="why the gate did not pass; REQUIRED for outcomes failed, unavailable, "
            "partial and skipped (--evidence is what you observed, not a substitute)",
        )
        gr.add_argument("--evidence", default="", help="what you observed: output, a summary")
        gr.add_argument("--command", default="")
        gr.add_argument("--exit-code", type=int)
        gr.add_argument(
            "--model",
            default="",
            help="the REVIEWER's model, for family independence (the author's is "
            "`complete --model`); an author-family name on a reviewer gate is refused",
        )
        if name == "record":
            gr.add_argument(
                "--reviewer-model",
                default="",
                help="like --model, stating that this model IS the reviewer, so an "
                "author-family name is recorded rather than refused",
            )
            gr.add_argument(
                "--reviewed-sha",
                default="",
                help="the commit the review tool ran on (roborev review <sha>); refused "
                "when it is not the item's branch head or a commit of its branch",
            )
        gr.add_argument("--output-file", default="")
        gr.set_defaults(fn=cmd_gate)

    cp = s.add_parser("complete", help="finish an item (exit 3 = gates not satisfied)")
    cp.add_argument("id")
    cp.add_argument("--sha", default="")
    cp.add_argument("--model", default="", help="the AUTHOR's model, for independence check")
    cp.add_argument("--force", action="store_true")
    cp.add_argument(
        "--changelog",
        default="",
        help="'Added: text' (Added|Changed|Deprecated|Removed|Fixed|Security), or "
        "skip / internal to keep it out of the changelog; optional",
    )
    cp.add_argument(
        "--regression-test",
        action="append",
        default=[],
        help="for a fix task: the test that now guards the bug(s) it fixes; closes them "
        "(as `bug fixed` would) and completes. Repeat for several.",
    )
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
    bl.add_argument(
        "--reopen",
        action="store_true",
        help="block an item that is already DONE or ABANDONED (refused without)",
    )
    bl.set_defaults(fn=cmd_block)

    ub = s.add_parser(
        "unblock", help="release a blocked item back into the queue (exit 2 = not blocked)"
    )
    ub.add_argument("id")
    ub.add_argument("--note", default="", help="why it is work again")
    ub.set_defaults(fn=cmd_unblock)

    mg = s.add_parser("merge", help="merge an item's branch from the primary checkout")
    mg.add_argument("id")
    mg.add_argument("--message", default="")
    mg.add_argument("--keep", action="store_true")
    mg.add_argument("--allow-dirty", action="store_true")
    mg.add_argument(
        "--allow-empty",
        action="store_true",
        help="land a branch with no commits ahead of its target (refused by default: it "
        "would record the item merged with nothing landed)",
    )
    mg.add_argument(
        "--branch",
        default="",
        help="for an item claimed without a worktree: the branch to land (default: the one "
        "checked out in the worktree you are standing in)",
    )
    mg.add_argument(
        "--model",
        default="",
        help="the AUTHOR's model. In PR mode completion happens later, at `pr sync`, and "
        "the reviewer-independence check needs it then",
    )
    mg.set_defaults(fn=cmd_merge)

    pr = s.add_parser(
        "pr", help="pull/merge requests: what reviewers did ([flow].integration = pr)"
    )
    pr_s = pr.add_subparsers(dest="pr_cmd", required=True)
    psy = pr_s.add_parser(
        "sync",
        help="ask the forge about every request in review: complete merged ones, reopen "
        "ones with requested changes, merge approved ones",
    )
    psy.add_argument("--item", default="", help="only this item")
    psy.set_defaults(fn=cmd_pr)
    pst = pr_s.add_parser("status", help="every item's request, from the log (no forge call)")
    pst.set_defaults(fn=cmd_pr)
    pth = pr_s.add_parser(
        "threads",
        help="an item's review threads from the forge; with --thread, reply and/or resolve one",
    )
    pth.add_argument("id")
    pth.add_argument("--thread", default="", help="the thread's id (as listed)")
    pth.add_argument("--reply", default="", help="post this reply on --thread")
    pth.add_argument("--resolve", action="store_true", help="mark --thread resolved")
    pth.set_defaults(fn=cmd_pr)

    ver = s.add_parser("version", help="version tags: the next version, and cutting it")
    ver_s = ver.add_subparsers(dest="version_cmd", required=True)
    vsh = ver_s.add_parser("show", help="current version, next version, why, release notes")
    vsh.add_argument("--bump", default="", choices=["", "major", "minor", "patch"])
    vsh.add_argument("--line", default="", help="a maintenance line (default: the current one)")
    vsh.set_defaults(fn=cmd_version)
    vct = ver_s.add_parser(
        "cut", help="tag the next version (gitflow: via a release branch, or a release request)"
    )
    vct.add_argument("--bump", default="", choices=["", "major", "minor", "patch"])
    vct.add_argument("--version", dest="set_version", default="", help="exact MAJOR.MINOR.PATCH")
    vct.add_argument(
        "--push", action="store_true", help="publish the tag (and branches) to the remote"
    )
    vct.add_argument("--dry-run", action="store_true")
    vct.add_argument("--line", default="", help="a maintenance line (default: the current one)")
    vct.add_argument(
        "--changelog",
        action="store_true",
        help="also write the version's section into CHANGELOG.md, committed with the cut "
        "(in a release request under gitflow + pr); never written without this flag",
    )
    vct.add_argument(
        "--force", action="store_true", help="with --changelog: replace a hand-edited changelog"
    )
    vct.set_defaults(fn=cmd_version)

    pm = s.add_parser(
        "promote",
        help="environment branches: move work one step downstream ([flow].environments)",
    )
    pm_s = pm.add_subparsers(dest="promote_cmd", required=True)
    pma = pm_s.add_parser("add", help="file a promotion to ENV from the branch just upstream of it")
    pma.add_argument("env")
    pma.add_argument("--force", action="store_true", help="file it even with nothing to carry")
    pma.set_defaults(fn=cmd_promote)
    pmd = pm_s.add_parser(
        "deployed", help="record the sha a deploy put live in ENV (call it from the deploy hook)"
    )
    pmd.add_argument("env")
    pmd.add_argument("--sha", default="", help="the deployed commit (default: ENV's branch head)")
    pmd.set_defaults(fn=cmd_promote)
    pm_s.add_parser(
        "status", help="each environment: head, commits behind upstream, open promotion"
    ).set_defaults(fn=cmd_promote)

    fl = s.add_parser(
        "flow",
        help="how this project works: branching model, release lines, and every workflow "
        "choice with who made it",
    )
    fl_s = fl.add_subparsers(dest="flow_cmd", required=True)
    fl_s.add_parser("show", help="every choice: value, options, and who decided").set_defaults(
        fn=cmd_flow
    )
    fch = fl_s.add_parser(
        "choose", help="record a workflow choice (the config file still wins over it)"
    )
    fch.add_argument("knob")
    fch.add_argument("value")
    fch.add_argument("--reason", default="", help="why — the next agent reads this")
    fch.set_defaults(fn=cmd_flow)

    br = s.add_parser("brief", help="budgeted session-start pack")
    br.add_argument("--item", default="")
    br.add_argument("--phase", default="")
    br.add_argument(
        "--check-recovery", action="store_true", default=A_LIFECYCLE.DEFAULT_CHECK_RECOVERY
    )
    br.set_defaults(fn=cmd_brief)

    ex = s.add_parser("external", help="dependencies on items in sibling repositories")
    ex_s = ex.add_subparsers(dest="external_cmd", required=True)
    ex_s.add_parser(
        "sync", help="observe the sibling-repo items `needs` names; record what changed"
    ).set_defaults(fn=cmd_external)

    jb = s.add_parser("job", help="long-running processes an item waits on: run, add, list, end")
    jb_s = jb.add_subparsers(dest="job_cmd", required=True)
    jr = jb_s.add_parser("run", help="launch a command detached for an item and record it")
    jr.add_argument("item")
    jr.add_argument("command", help="shell command (quote it)")
    jr.add_argument("--log", default="", help="output file (default .ddflow/local/jobs/)")
    jr.add_argument("--cwd", default="", help="default: the item's worktree, else the repo")
    jr.set_defaults(fn=cmd_job)
    ja = jb_s.add_parser("add", help="register a process started some other way")
    ja.add_argument("item")
    ja.add_argument("--pid", type=int, required=True)
    ja.add_argument("--command", default="")
    ja.add_argument("--log", default="")
    ja.set_defaults(fn=cmd_job)
    jl = jb_s.add_parser("list", help="jobs with their live status (exit 2 = none)")
    jl.add_argument("--item", default="")
    jl.add_argument("--all", action="store_true", help="include ended jobs")
    jl.set_defaults(fn=cmd_job)
    je = jb_s.add_parser("end", help="record how a job ended (refused while it runs)")
    je.add_argument("job")
    je.add_argument("--exit-code", type=int, default=None, help="default: the one its log recorded")
    je.add_argument("--note", default="")
    je.add_argument(
        "--force", action="store_true", help="end a job on another host you have checked there"
    )
    je.set_defaults(fn=cmd_job)

    me = s.add_parser(
        "memory", help="operational facts about this machine/repo, shown at session start"
    )
    me_s = me.add_subparsers(dest="memory_cmd", required=True)
    ma = me_s.add_parser("add", help="remember one fact (refused over [memory] max_chars)")
    ma.add_argument("text")
    ma.add_argument("--tags", default="")
    ma.add_argument("--id", default="", help="re-record (correct) an existing memory")
    dedupe_flags.add_flags(ma)
    ma.set_defaults(fn=cmd_memory)
    ml = me_s.add_parser("list", help="live memories, newest first (exit 2 = none)")
    ml.add_argument("--query", default="", help="rank by relevance instead of recency")
    ml.add_argument("--limit", type=int, default=0)
    ml.add_argument("--all", action="store_true", help="include forgotten memories")
    ml.set_defaults(fn=cmd_memory)
    mf = me_s.add_parser("forget", help="stop believing a memory; kept, with the reason")
    mf.add_argument("id")
    mf.add_argument("--reason", required=True)
    mf.set_defaults(fn=cmd_memory)

    ls = s.add_parser("lesson")
    ls_s = ls.add_subparsers(dest="lesson_cmd", required=True)
    la = ls_s.add_parser("add")
    la.add_argument("--id", default="")
    la.add_argument("--title", required=True)
    la.add_argument("--rule", default="")
    la.add_argument("--why", default="")
    la.add_argument("--how", default="")
    la.add_argument(
        "--summary",
        default="",
        help="the lesson in one paragraph; what docs/ddflow/LESSONS-SUMMARY.md is made of",
    )
    la.add_argument("--tags", default="")
    la.add_argument("--seen-in", default="")
    la.add_argument("--supersedes", default="")
    la.add_argument(
        "--pattern",
        default="",
        help="regex this lesson forbids. Scans NOW and stores WHICH sites match, so "
        "`lesson verify` can name what reappeared — a count could only say it got worse",
    )
    la.add_argument(
        "--globs", default="", help="comma-separated globs to scan (default: all tracked files)"
    )
    dedupe_flags.add_flags(la)
    la.set_defaults(fn=cmd_lesson)
    lv = ls_s.add_parser(
        "verify",
        help="re-scan every lesson's pattern and name the sites that reappeared",
    )
    lv.set_defaults(fn=cmd_lesson)
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
        help="comma-separated subset: decisions,lessons,memories,research,bugs,items,prompts",
    )
    rc.add_argument("--max-chars", type=int, default=4000)
    rc.set_defaults(fn=cmd_recall)

    sm = s.add_parser(
        "similar",
        help="'is this already filed?' -- the existing bugs, tasks, lessons and other "
        "records most like a text, before you add it (read-only; exit 2 when none)",
    )
    sm.add_argument("text", help="the title or summary of the record you are about to file")
    sm.add_argument(
        "--kind",
        default="",
        help="comma-separated subset of [dedupe].kinds: "
        "bug,task,phase,lesson,decision,research,memory (default: all of them)",
    )
    sm.set_defaults(fn=cmd_similar)

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
    dedupe_flags.add_flags(dca)
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

    ru = s.add_parser("rule", help="project rules: add, edit, list, search, show, remove")
    ru_s = ru.add_subparsers(dest="rule_cmd", required=False)
    rua = ru_s.add_parser("add")
    rua.add_argument("--id", required=True)
    rua.add_argument("--title", required=True)
    rua.add_argument("--content", default="")
    rua.add_argument("--tags", default="")
    rua.add_argument("--scope", default="project")
    rua.add_argument("--priority", type=int, default=None)
    rua.add_argument("--globs", default="")
    g = rua.add_mutually_exclusive_group()
    g.add_argument("--new", action="store_true", help="a different rule, file it")
    g.add_argument("--extends", metavar="ID", default="")
    g.add_argument("--duplicate-of", dest="duplicate_of", metavar="ID", default="")
    g.add_argument("--related", metavar="ID", default="")
    g.add_argument("--check", action="store_true", help="dry run: list duplicates only")
    rua.set_defaults(fn=cmd_rule)
    rue = ru_s.add_parser("edit")
    rue.add_argument("id")
    for flag in ("title", "content", "tags", "scope", "globs"):
        rue.add_argument(f"--{flag}", default=None)
    rue.add_argument("--priority", type=int, default=None)
    rue.set_defaults(fn=cmd_rule)
    rul = ru_s.add_parser("list")
    rul.add_argument("--tag", default="")
    rul.add_argument("--scope", default="")
    rul.set_defaults(fn=cmd_rule)
    rus = ru_s.add_parser("search")
    rus.add_argument("query")
    rus.add_argument("--limit", type=int, default=10)
    rus.add_argument("--exact", action="store_true")
    rus.add_argument("--regex", action="store_true")
    rus.add_argument("--tag", default="")
    rus.add_argument("--scope", default="")
    rus.set_defaults(fn=cmd_rule)
    rush = ru_s.add_parser("show")
    rush.add_argument("id")
    rush.set_defaults(fn=cmd_rule)
    rur = ru_s.add_parser("remove")
    rur.add_argument("id")
    rur.set_defaults(fn=cmd_rule)
    ru.set_defaults(fn=cmd_rule, rule_cmd="list", tag="", scope="")

    add_verify_parser(s)
    add_ci_parser(s)

    stt = s.add_parser("status", help="one answer to 'what is the state of this project?'")
    stt.set_defaults(fn=cmd_status)

    rs = s.add_parser("research")
    # `ddflow research add ...` as well as `ddflow research ...`: `lesson add`, `decision
    # add` and `memory add` all take the verb, the MCP tool is `ddflow_research_add`, and
    # the research gate's instruction says `research add` -- which this parser rejected
    # as "unrecognized arguments: add" for every agent that followed it.
    rs.add_argument(
        "verb", nargs="?", choices=["add"], help="optional: `research add` = `research`"
    )
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
    dedupe_flags.add_flags(rs)
    rs.set_defaults(fn=cmd_research)

    bg = s.add_parser("bug")
    bg_s = bg.add_subparsers(dest="bug_cmd", required=True)
    bf = bg_s.add_parser("found")
    bf.add_argument("--id", default="")
    bf.add_argument("--summary", required=True)
    bf.add_argument("--item", default="")
    bf.add_argument("--title", default="", help="a short headline for the bug")
    bf.add_argument("--severity", default="", help="low | medium | high | critical (optional)")
    bf.add_argument(
        "--scope",
        default="",
        help="project (default), or ddflow for a bug in ddflow itself",
    )
    bf.add_argument(
        "--globs",
        default="",
        help="the fix task's files (comma-separated); default: the item's own globs",
    )
    bf.add_argument(
        "--no-task",
        action="store_true",
        help="file no fix task: the bug is fixed in the commit that found it",
    )
    dedupe_flags.add_flags(bf)
    bf.set_defaults(fn=cmd_bug)
    bft = bg_s.add_parser(
        "file-tasks",
        help="file a fix task for every open bug that has none (one-shot, after an upgrade)",
    )
    bft.add_argument("--dry-run", action="store_true", help="list what would be filed")
    bft.set_defaults(fn=cmd_bug)
    bx = bg_s.add_parser("fixed")
    bx.add_argument("id")
    bx.add_argument(
        "--regression-test",
        action="append",
        default=[],
        help="the test that now guards this bug; repeat it, or separate with ',' or ';', "
        "for several",
    )
    bx.add_argument("--lesson", default="")
    bx.add_argument("--lesson-title", default="")
    bx.add_argument("--lesson-rule", default="")
    bx.add_argument(
        "--changelog", default="", help="'Fixed: text' (any category), or skip / internal"
    )
    bx.set_defaults(fn=cmd_bug)
    bv = bg_s.add_parser(
        "invalid", help="close a bug as a FALSE finding (never as fixed), with why and the probe"
    )
    bv.add_argument("id")
    bv.add_argument("--reason", required=True, help="why the finding is false")
    bv.add_argument(
        "--evidence", default="", help="the probe command or test node id that showed it"
    )
    bv.set_defaults(fn=cmd_bug)
    add_bug_reopen_parser(bg_s)

    se = s.add_parser("session")
    se_s = se.add_subparsers(dest="session_cmd", required=True)
    ss = se_s.add_parser("start")
    ss.add_argument("--model", default="")
    ss.add_argument("--tool", default="")
    ss.set_defaults(fn=cmd_session)
    sp = se_s.add_parser("prompt")
    sp.add_argument(
        "session",
        nargs="?",
        default="",
        help="the SESSION id (from `session start`), not the text; omitted: the latest "
        "open session, else an implicit new one",
    )
    sp.add_argument("--text", help="the prompt text; without it, read from piped stdin")
    sp.add_argument("--item", default="")
    sp.set_defaults(fn=cmd_session)
    sn = se_s.add_parser("note")
    sn.add_argument(
        "session",
        nargs="?",
        default="",
        help="the SESSION id (from `session start`), not the text; omitted: the latest "
        "open session, else an implicit new one",
    )
    sn.add_argument("--text", help="the note text; without it, read from piped stdin")
    sn.add_argument("--item", default="")
    sn.set_defaults(fn=cmd_session)
    so = se_s.add_parser(
        "adopt-orphans", help="attach prompts/notes recorded with no session id to a session"
    )
    so.set_defaults(fn=cmd_session)
    sd = se_s.add_parser("end")
    sd.add_argument("session")
    sd.add_argument("--summary", default="")
    sd.set_defaults(fn=cmd_session)
    add_session_view_parsers(se_s)

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
        help="print ONE view to stdout instead of writing files: lessons, lessons-summary, research, board",
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
    cf.add_argument(
        "value",
        nargs="*",
        default=[],
        help="the value, when --set is used; or `KEY VALUE` with no --set "
        "(ddflow config review.max_rounds 0 --local)",
    )
    cf.add_argument(
        "--append-toml",
        default="",
        help="append this TOML to .ddflow/config.toml (validated first)",
    )
    cf.add_argument(
        "--local",
        action="store_true",
        help="write --set/--append-toml to the git-ignored .ddflow/local/config.toml: "
        "this machine's endpoints, hosts, key variables and sizing, never committed",
    )
    cf.set_defaults(fn=cmd_config)

    add_export_parser(s)
    add_bisect_parser(s)

    cd = s.add_parser("cadence", help="which periodic passes are due (exit 2 = none)")
    cd.add_argument("--ran", default="")
    cd.add_argument("--note", default="")
    cd.set_defaults(fn=cmd_cadence)

    pn = s.add_parser(
        "pins",
        help="which text of an instruction file a test pins, before you compress it",
    )
    pn.add_argument("document", help="the instruction file, e.g. AGENTS.md")
    pn.add_argument("--tests", default="", help="comma-separated test dirs (default: tests,test)")
    pn.add_argument(
        "--min-needle",
        type=int,
        default=None,
        help="shortest literal that counts as a pin (default 12)",
    )
    pn.add_argument("--top", type=int, default=10, help="how many free stretches to show")
    pn.set_defaults(fn=cmd_pins)

    ts = s.add_parser(
        "tests",
        help="the tests your change reaches, and a parallel command to run them (exit 2 = none)",
    )
    ts.add_argument("--item", default="", help="an item id: use its worktree and base")
    ts.add_argument("--base", default="", help="compare against this ref (default: the base)")
    ts.set_defaults(fn=cmd_tests)

    pc = s.add_parser(
        "precommit",
        help="propose a .pre-commit-config.yaml for this repository's stacks "
        "(writes nothing without --write)",
    )
    pc.add_argument(
        "--ddflow-cmd",
        default="ddflow",
        help="how the proposed local hooks reach ddflow (default: `ddflow` on PATH)",
    )
    pc.add_argument(
        "--write",
        action="store_true",
        help="create .pre-commit-config.yaml; an existing one is never replaced (exit 3)",
    )
    pc.set_defaults(fn=cmd_precommit)

    rv = s.add_parser("reviewers", help="find, list and test cross-family reviewers")
    rv_s = rv.add_subparsers(dest="reviewers_cmd", required=True)
    rvd = rv_s.add_parser("detect", help="probe well-known local endpoints")
    rvd.add_argument(
        "--write",
        action="store_true",
        help="append the discovered reviewers to the git-ignored .ddflow/local/reviewers.toml",
    )
    rvd.add_argument(
        "--shared",
        action="store_true",
        help="with --write: commit them to .ddflow/config.toml instead, for every clone",
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
        "--shared",
        action="store_true",
        help="write to the committed .ddflow/config.toml instead of the git-ignored "
        ".ddflow/local/reviewers.toml -- only for a reviewer every clone should use",
    )
    rva.add_argument(
        "--no-launch",
        action="store_true",
        help="do not auto-start a local server for this reviewer",
    )
    rva.set_defaults(fn=cmd_reviewers)
    rvp = rv_s.add_parser(
        "approve",
        help="a PERSON vouches for a tool-written reviewer entry (refused under an agent "
        "identity); with no name, list the entries waiting (anyone may)",
    )
    rvp.add_argument("name", nargs="?", default="")
    rvp.add_argument("--note", default="")
    rvp.set_defaults(fn=cmd_reviewers)
    rvt = rv_s.add_parser("test", help="send a tiny known-buggy diff and check the reply")
    rvt.add_argument("name", nargs="?", default="")
    rvt.set_defaults(fn=cmd_reviewers)

    rw = s.add_parser(
        "review",
        help="run the configured reviewer(s) over an item's diff",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="two commands share this parser:\n"
        "  ddflow review <id> --gate G [--chunk N | --delta]   run the reviewer(s) (slow, shared);\n"
        "                  a gate gets [review].max_rounds (default 2) full rounds, then --delta/triage\n"
        "  ddflow review triage <id> --gate G --finding N --refuted|--confirmed --probe ...\n"
        "                                            record what became of one finding\n"
        "--finding/--refuted/--confirmed/--probe belong to the second form only. It REQUIRES the\n"
        "`triage` verb: without it they are refused, never run as a review.",
    )
    # `*`, not `?`: `ddflow review triage <id> ...` is a verb followed by the item.
    rw.add_argument("id", nargs="*", default=[], help="the item; or `triage <item>`")
    rw.add_argument("--gate", default=None, help="gate to review (default critic)")
    rw.add_argument(
        "--intent",
        default="",
        help="what the change is meant to do (defaults to the item's title/body)",
    )
    rw.add_argument("--context", default="")
    rw.add_argument("--base", default="")
    rw.add_argument(
        "--commit",
        default="",
        help="review this one landed commit (vs its first parent) instead of the item's branch",
    )
    rw.add_argument(
        "--branch",
        default="",
        help="review this branch against base -- for an item claimed without a worktree "
        "(default: the branch checked out in the worktree you are standing in)",
    )
    rw.add_argument(
        "--chunk",
        action="append",
        default=[],
        help="re-review only chunk N (as the recorded review numbered it; repeatable, or "
        "'2,5') and merge it into that record -- same diff, chunk size and reviewer",
    )
    rw.add_argument(
        "--delta",
        action="store_true",
        help="recheck ONLY what changed since the head the gate's last review covered: "
        "not a full round, never refused by [review].max_rounds (this is the default once "
        "the gate has a recorded review; [review].delta_default = false turns that off)",
    )
    rw.add_argument(
        "--full",
        action="store_true",
        help="review the item's WHOLE diff even though the gate has a recorded review: a "
        "full round, counted against [review].max_rounds",
    )
    rw.add_argument(
        "--force",
        action="store_true",
        help="run a full round past [review].max_rounds; needs --reason, recorded in the evidence",
    )
    rw.add_argument("--reason", default="", help="why --force")
    rw.add_argument("--finding", type=int, default=None, help="triage: the finding's number (#N)")
    rw.add_argument(
        "--refuted", action="store_true", help="triage: --probe shows the finding is false"
    )
    rw.add_argument(
        "--confirmed", action="store_true", help="triage: --probe is the fix/test that answers it"
    )
    rw.add_argument("--probe", default=None, help="triage: the evidence for the verdict")
    rw.set_defaults(fn=cmd_review)

    ad = s.add_parser("adopt", help="install ddflow into this project for one or more agents")
    ad.add_argument(
        "--agents",
        default="",
        # GENERATED from the registry, never typed. A hand-kept list here drifted the
        # moment `cursor` was added, and `test_every_supported_agent_is_named_where_a_user
        # _would_look` exists because of it: a capability nobody can find is one nobody
        # uses. Generating it means adding an agent cannot leave this behind.
        help=f"comma-separated: {','.join(AGENT_TARGETS)} (default: all)",
    )
    ad.add_argument("--docs", default="docs/ddflow", help="where to write the drivers")
    ad.add_argument(
        "--launch",
        default="auto",
        choices=["auto", "uvx", "docker", "python"],
        help="how agents spawn the MCP server: 'auto' prefers uvx for an install from a "
        "package index and this installation otherwise; 'docker' needs no Python "
        "toolchain at all",
    )
    ad.add_argument(
        "--image",
        default="ghcr.io/OWNER/ddflow:latest",
        help="container image used by --launch docker",
    )
    ad.add_argument(
        "--refresh-docs",
        action="store_true",
        help="rewrite ONLY the driver docs, the AGENTS.md/CLAUDE.md blocks and the agents' "
        "native rules from this ddflow's templates; leaves MCP launches, hooks and command "
        "files alone (what `doctor` points to when drivers lag)",
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
    hi.add_argument("--agent", dest="log_agent", default="", help="only this agent's shard")
    hi.add_argument("--tail", type=_positive_int, default=0, help="the last N events, oldest first")
    hi.set_defaults(fn=cmd_history)

    wf = s.add_parser(
        "workflow",
        help="the rules this project runs by, and how to change them (exit 1 = incoherent)",
    )
    wfs = wf.add_subparsers(dest="workflow_cmd")
    wf.set_defaults(fn=cmd_workflow, dry_run=False)

    wfs.add_parser("state", help="one-page overview: workflow, rules, queue, bugs, diagram")

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
        help="one of: " + ", ".join(sorted(help_topics())) + ". Omit for the overview.",
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
    for _p, _dflt in ((co, False), (co_list, argparse.SUPPRESS)):
        # On the bare `companions` AND on `list`, so `companions --verify` works. The
        # subparser's default is SUPPRESS so it cannot overwrite a value the parent parsed.
        _p.add_argument(
            "--verify",
            action="store_true",
            default=_dflt,
            help="launch each MCP companion and require an answer to `initialize` (spawns "
            "processes; opt-in; exit 1 = one is not an MCP server, 2 = could not tell)",
        )
        _p.add_argument(
            "--id",
            default="" if _dflt is False else argparse.SUPPRESS,
            help="with --verify: comma-separated ids to launch (even if not installed)",
        )
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

    hk = s.add_parser(
        "hooks", help="install/inspect the enforcement git hook and the Claude Code session hook"
    )
    hk_s = hk.add_subparsers(dest="hooks_cmd", required=True)
    hki = hk_s.add_parser("install")
    hki.add_argument(
        "--force",
        action="store_true",
        help="replace an existing pre-commit hook ddflow does not manage",
    )
    _claude_help = (
        "the Claude Code SessionStart hook in .claude/settings.json instead of the git "
        "hook; other hooks there are left exactly as they are"
    )
    hki.add_argument("--claude", action="store_true", help=_claude_help)
    _gemini_help = "the Gemini CLI BeforeAgent prompt hook in .gemini/settings.json"
    hki.add_argument("--gemini", action="store_true", help=_gemini_help)
    hki.set_defaults(fn=cmd_hooks)
    hku = hk_s.add_parser("uninstall")
    hku.add_argument("--claude", action="store_true", help=_claude_help)
    hku.add_argument("--gemini", action="store_true", help=_gemini_help)
    hku.set_defaults(fn=cmd_hooks)
    hk_s.add_parser("status").set_defaults(fn=cmd_hooks)
    hk_s.add_parser("check-commit", help="(invoked by the hook)").set_defaults(fn=cmd_hooks)
    hkm = hk_s.add_parser("check-msg", help="(invoked by the commit-msg hook)")
    hkm.add_argument("msg_file", help="the message file git passes the hook")
    hkm.set_defaults(fn=cmd_hooks)
    hk_s.add_parser(
        "session-start",
        help="(invoked by the Claude Code SessionStart hook) print the brief; always exit 0",
    ).set_defaults(fn=cmd_hooks)

    hkp = hk_s.add_parser(
        "prompt",
        help="(invoked by the UserPromptSubmit / BeforeAgent hook) record the prompt on stdin; "
        "always exit 0",
    )
    hkp.add_argument("--gemini", action="store_true", help="answer with the JSON Gemini CLI wants")
    hkp.set_defaults(fn=cmd_hooks)

    s.add_parser("mcp", help="run the MCP stdio server over this repository").set_defaults(
        fn=cmd_mcp
    )
    register_list_viewers(s)
    _accept_global_options_anywhere(p)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # What was typed, for the commands a refusal tells the caller to run instead.
    args._argv = list(sys.argv[1:] if argv is None else argv)
    try:
        ctx = Ctx(args)
        if getattr(args, "allow_older", False):
            reason = getattr(args, "skew_reason", "") or str(getattr(args, "reason", "") or "")
            if ctx.log.override_skew(reason) is None:
                print(
                    "ddflow: nothing to override (no skew); --allow-older-version ignored",
                    file=sys.stderr,
                )
        return int(args.fn(args, ctx))
    except KeyboardInterrupt:
        return 130
    except SkewRefused as exc:
        print(str(exc), file=sys.stderr)
        return REFUSED
    except L.LeaseError as exc:
        print(str(exc), file=sys.stderr)
        return REFUSED
    except (W.GitError, ValueError, KeyError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return FAIL


if __name__ == "__main__":
    raise SystemExit(main())

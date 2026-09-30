"""`cleanup`, `cadence`, `import` — the human surface for `api.operations`."""

from __future__ import annotations

import json
import sys

from ...api import operations as A
from ..context import FAIL, NOTHING, OK, Ctx


def cmd_cleanup(a, c: Ctx) -> int:
    """Classify every ddflow worktree and branch; with --apply, land the safe ones."""
    out = A.cleanup(c.repo, apply=a.apply, agent=c.requested_agent)
    if c.json and not a.apply:
        print(json.dumps(out.body(("trees", "stale_branches")), indent=2, default=str))
        return out.exit
    if out.exit == NOTHING:
        print(out.reason)
        return NOTHING
    plan = out.data["_render"]["plan"]
    for t in plan.trees + plan.stale_branches:
        print("  " + t.render())
    if plan.needs_human:
        print(
            f"\n{len(plan.needs_human)} tree(s) hold UNCOMMITTED work and are never "
            f"touched automatically. Inspect each before deciding."
        )
    if not a.apply:
        print(
            f"\n{out.data['actionable']} safe action(s) available. Re-run with --apply "
            f"to perform them; dirty trees are excluded whatever you pass."
        )
        return OK
    for line in out.data["performed"]:
        print(f"  {line}")
    c.out(f"\n{len(out.data['performed'])} action(s) performed.", out.body(("performed",)))
    return OK


def cmd_cadence(a, c: Ctx) -> int:
    out = A.cadence(c.repo, ran=a.ran or "", note=a.note or "", agent=c.requested_agent)
    if out.exit == FAIL:
        # Printed nothing and exited 0 before `cadence` could fail at all.
        print(out.reason, file=sys.stderr)
        return FAIL
    if a.ran:
        c.out(f"recorded cadence run: {a.ran}", out.body(("cadence",)))
        return OK
    if c.json:
        print(json.dumps(out.body("due"), indent=2))
        return out.exit
    if out.exit == NOTHING:
        print(out.reason)
        return NOTHING
    for d in out.data["due"]:
        print(f"DUE: {d['cadence']} — {d['since']} {d['unit']} since last (every {d['every']})")
    print("\nRecord one with: ddflow cadence --ran <name>")
    return OK


def _import_verify(c: Ctx) -> int:
    out = A.import_verify(c.repo, agent=c.requested_agent)
    if c.json:
        print(json.dumps(out.body(), indent=2, default=str))
        return out.exit
    r = out.data["_render"]["report"]
    if out.exit == NOTHING:
        print(out.reason)
        for n in r.notes:
            print(f"  {n}")
        return NOTHING

    when = f" between {r.first_at[:10]} and {r.last_at[:10]}" if r.first_at else ""
    lines = [f"Imported{when}:", ""]
    lines += [f"  {n:>6} {kind}(s)" for kind, n in sorted(r.imported.items())]
    if r.unstructured:
        lines.append(f"  {len(r.unstructured):>6} with prose-only provenance (older import)")
    lines.append("")
    if r.findings:
        lines.append("Left to decide or fix:")
        lines += [f"  - {f}" for f in r.findings]
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
    return out.exit


def cmd_import(a, c: Ctx) -> int:
    """Propose what an existing project already has, so the queue starts where it is.

    Reads and reports by default; `--apply` writes. Exit 2 when there is nothing to
    propose — "no data" reported as itself.
    """
    from ...services import importer as IM

    if a.verify:
        # `--include-done` and `--max-tasks` shape an IMPORT. Reading past them here would
        # be the silent-knob-drop shape, standing next to a flag that is loudly refused
        # two lines down.
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
            # picking one silently is how an operator who asked to import ends up having
            # only looked -- or worse, the other way round.
            print(
                "--verify and --apply ask for different things: one reports on the "
                "import that happened, the other performs one. Run them separately.",
                file=sys.stderr,
            )
            return FAIL
        return _import_verify(c)

    out = A.import_project(
        c.repo,
        apply=a.apply,
        include_done=a.include_done,
        max_tasks=a.max_tasks,
        agent=c.requested_agent,
    )
    if out.data["applied"]:
        c.out(
            "Imported: "
            + ", ".join(f"{v} {k}" for k, v in sorted(out.data["written"].items()))
            + "\n  Every item records where it came from. Review with `ddflow board`, "
            "then give each task its globs — an item with no declared globs is one the "
            "conflict detector cannot protect.",
            out.body(),
        )
        return OK
    if c.json:
        print(json.dumps(out.body(), indent=2, default=str))
        return out.exit
    plan = out.data["_render"]["plan"]
    if out.exit == NOTHING:
        print(out.reason)
        for n in plan.notes + plan.source_notes:
            print(f"  {n}")
        return NOTHING

    preview = out.data["_render"]["preview_rows"]
    lines = ["What this project already has (nothing written yet):", ""]
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
    lines += [f"  NOTE: {n}" for n in plan.notes + plan.source_notes]
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


def cmd_external(a, c: Ctx) -> int:
    from ...api import operations as AO

    out = AO.external_sync(c.repo, agent=c.requested_agent)
    if c.json:
        print(json.dumps(out.body("observed"), indent=2, default=str))
        return out.exit
    if out.exit == NOTHING:
        print(out.reason)
        return NOTHING
    for o in out.data["observed"]:
        if o["error"]:
            print(f"  {o['dep']}: NOT OBSERVED -- {o['error']}")
        else:
            mark = "  (changed)" if o["changed"] else ""
            print(f"  {o['dep']}: {o['state']}{mark}  {o['title'][:70]}")
    return out.exit


def cmd_pins(a, c: Ctx) -> int:
    """Which text of an instruction file is pinned by a test, and which is free."""
    tests = tuple(t.strip() for t in (a.tests or "").split(",") if t.strip())
    out = A.pins(c.repo, a.document, tests=tests, min_chars=a.min_needle, top=a.top)
    if out.exit == FAIL:
        print(out.reason, file=sys.stderr)
        return FAIL
    if c.json:
        print(json.dumps(out.body(), indent=2))
        return out.exit
    if out.exit == NOTHING:
        print(out.reason)
        return NOTHING
    d = out.data
    print(
        f"{d['document']}: {d['chars']} chars, {d['pinned_chars']} pinned by "
        f"{len(d['pins'])} needle(s), {d['free_chars']} free ({d['scanned']} test file(s) read)"
    )
    if d["unparsed"]:
        print(
            f"  NOTE: {len(d['unparsed'])} test file(s) did not parse and were read by a "
            f"lexer instead (all their quoted text counts as pinned), or could not be read "
            f"at all: {', '.join(d['unparsed'])}"
        )
    if d["tests"]:
        print("\nAfter editing it, re-run the suites that pin it:")
        print(f"  pytest {' '.join(d['tests'])}")
    for f in d["free"]:
        a_, b_ = f["lines"]
        where = f"line {a_}" if a_ == b_ else f"lines {a_}-{b_}"
        print(f"\n--- {f['chars']} chars free, {where} ---\n{f['text']}")
    print("\nFree means no test holds it, not that it is not a rule. Read what you delete.")
    return OK


def cmd_tests(a, c: Ctx) -> int:
    """The tests the change reaches, and the parallel command to run them (B16)."""
    out = A.relevant_tests(c.repo, item=a.item, where=c.called_from, base=a.base)
    if out.exit == FAIL:
        print(out.reason, file=sys.stderr)
        return FAIL
    if c.json:
        print(json.dumps(out.body(), indent=2))
        return out.exit
    d = out.data
    if out.exit == NOTHING:
        print(out.reason)
    else:
        print(
            f"{len(d['tests'])} test file(s) reach {len(d['changed'])} changed file(s) since {d['base']}:"
        )
        for t in d["tests"]:
            print(f"  {t['path']}  -- {t['reason']}")
        if d["command"]:
            print(f"\nRun them now, in parallel:\n  {d['command']}")
    if d["full_suite"]:
        print(f"\nThe unit_tests gate still runs the whole suite:\n  {d['full_suite']}")
    if d["advice"]:
        print(f"  NOTE: that command {d['advice']}")
    return out.exit


def cmd_precommit(a, c: Ctx) -> int:
    """A `.pre-commit-config.yaml` proposed for this repository's stacks."""
    out = A.precommit(c.repo, where=c.called_from, ddflow_cmd=a.ddflow_cmd, write=a.write)
    if out.exit == FAIL:
        print(out.reason, file=sys.stderr)
        return FAIL
    if c.json:
        print(json.dumps(out.body(), indent=2))
        if out.exit != OK and out.reason:
            print(out.reason, file=sys.stderr)  # a bare `{}` and an exit code say nothing
        return out.exit
    d = out.data
    if "stacks" not in d:  # refused before anything was proposed
        print(out.reason, file=sys.stderr)
        return out.exit
    stacks = ", ".join(sorted(d["stacks"])) or "none detected"
    if d["written"]:
        print(f"Wrote {d['path']} for: {stacks}.")
    elif out.exit != OK:
        print(out.reason, file=sys.stderr)
    else:
        print(d["text"], end="")
        where = "exists -- compare, and merge by hand" if d["exists"] else "does not exist yet"
        print(f"\n# {d['path']} {where}. --write creates it; it never replaces one.")
    if not d["ddflow_cmd_found"]:
        print(
            f"# NOTE: `{d['ddflow_cmd']}` is not found from here, and the ddflow hooks run it "
            "on every commit: pass --ddflow-cmd with a command that reaches ddflow."
        )
    if not d["ddflow_cmd_recognised"]:
        print(
            f"# NOTE: `ddflow hooks status` finds these hooks by 'ddflow' in their command, "
            f"so `{d['ddflow_cmd']}` will be reported NOT installed although it runs."
        )
    for prog, stages in d["missing"].items():
        print(
            f"# NOTE: {prog} is not found here; the hook that runs it would fail at "
            f"{'/'.join(stages)} until it is installed (or drop that hook)."
        )
    if d["ddflow_hooks_installed"]:
        print(
            f"# ddflow's own {', '.join(d['ddflow_hooks_installed'])} hook(s) are installed: "
            "`pre-commit install` would keep them as .legacy and run them beside the local "
            "hooks. Run `ddflow hooks uninstall` first."
        )
    # Said on EVERY run, not only the one that wrote the file: a config nobody activated
    # runs nothing, and the run after installing pre-commit is when this is needed.
    activate = f"`{d['activate']}` activates it."
    if d["installed"] is None:
        print("# Could not tell whether pre-commit is installed: its probe did not answer.")
    elif not d["installed"]:
        print("# pre-commit is not installed: ask the operator, then `pipx install pre-commit`.")
    print(f"# {activate}")
    return out.exit

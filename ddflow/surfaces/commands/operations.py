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
        for n in plan.notes:
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
    lines += [f"  NOTE: {n}" for n in plan.notes]
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

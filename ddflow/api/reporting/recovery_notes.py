"""How `doctor` words what `leases.scan` found."""

from __future__ import annotations

import os

from ...views.markdown import may_hold_work


def _recovery_findings(recs: list) -> tuple[list[str], list[str]]:
    """(problems, notes) for recovery records.

    A clean adopted orphan is the harness's own tree, left to the harness, so those fold
    into ONE summary note (counted per tree, as the handshake does) instead of one line
    each. Every other record keeps its own line.
    """
    problems: list[str] = []
    notes: list[str] = []
    quiet: list[tuple[str, str]] = []
    for r in recs:
        if r.kind == "orphan_worktree" and r.adopted and not may_hold_work(r):
            quiet.append((r.item, os.path.normpath(r.worktree)))
            continue
        # A tree holding work or one git could not measure; a RUNNING item nobody holds
        # stays a note, as `next` offers it to resume (B3f8c406fea).
        tree_at_risk = may_hold_work(r) and r.kind != "stale_running"
        (problems if tree_at_risk else notes).append(f"{r.kind}: {r.item} — {r.advice}")
    if quiet:
        names = sorted({i for i, _ in quiet})
        trees = len({t for _, t in quiet})
        shown = ", ".join(names[:5]) + (", ..." if names[5:] else "")
        notes.append(
            f"orphan_worktree: {len(names)} item(s) on {trees} adopted harness worktree(s) "
            f"holding no unsaved work, no lease held ({shown}) — the harness's own trees; "
            "leave them to the harness"
        )
    return problems, notes

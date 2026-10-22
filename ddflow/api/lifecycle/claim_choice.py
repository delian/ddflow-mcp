"""What a claim decides about the files and the tree it binds: the pieces `claim` calls.

* `_widened`: files named on a claim the caller already holds ADD to its lease.
* `_bug_default`: a fix task's bug globs as a default that never makes the claim wait.
* `_tree_choice`: which tree a claim bound and why.
"""

from __future__ import annotations

import time

from ...services import leases as L
from ...services.bug_globs import bug_globs_of
from ...services.completion import fixes_of


def _widened(want, prior, parked):
    """``want`` added to what the caller's own live lease already covers (Bf22edc42d5).

    Files named on a claim the caller already holds ADD to the lease; replacing them left
    the next commit uncovered. No files named leaves the claim alone."""
    if want is None or prior is None:
        return want
    have = list(prior.globs or (parked.globs if parked is not None else []))
    return have + [g for g in want if g not in have]


def _bug_default(st, cfg, it, me: str) -> tuple[list[str] | None, dict | None]:
    """(the files to claim, what to say about them) for a fix task that declared none.

    Its bug's files are a DEFAULT, so the commit hook does not refuse the fix the bug
    names -- never a reason to wait: those another agent's live lease holds are left off,
    and the answer says which files came from the bug and which were left off
    (B-claim-fix-task-takes-bug-globs). None when the bug declared no files."""
    named = bug_globs_of(st, it.id, cfg)
    if not named:
        return None, None
    now = time.time()
    left_off = [g for g in named if L.glob_clash(st, cfg, it, me, [g], now)]
    kept = [g for g in named if g not in left_off]
    return kept or None, {
        "bugs": sorted(fixes_of(st, it.id, cfg)),
        "kept": kept,
        "left_off": left_off,
    }


def _tree_choice(cfg, bound) -> dict[str, str]:
    """Which tree this claim bound and why: `how` is none | rebound | adopted | created.

    The rule is one and the same for every caller: the item's own tree from an earlier claim
    first; else the linked worktree the caller stands in, when no other item holds it; else a
    tree of its own under .ddflow/worktrees. It differed by caller with nothing said
    (B-claim-points-to-ddflow-worktrees-not-session), so every answer now names the case."""
    wt = bound.wt  # a claim._Bound
    if wt is None:
        return {
            "how": "none",
            "why": "no tree was asked for or needed (--no-worktree, a tree-less item, or [worktree].enabled is off)",
        }
    if bound.rebound:
        return {"how": "rebound", "why": "the item already has its own tree from an earlier claim"}
    if not wt.created and not bound.ours:
        return {
            "how": "adopted",
            "why": "you are standing in a linked worktree no other item holds",
        }
    if not cfg.worktree.adopt_existing:
        why = "[worktree].adopt_existing is off, so no tree is adopted"
    else:  # `_bind_tree` creates a tree only when adoption is off or `W.current` finds none
        why = "you are not standing in a linked worktree of this repository (the primary checkout, or a detached HEAD)"
    return {"how": "created", "why": why}

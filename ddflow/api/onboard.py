"""Onboarding preflight, in the layer the surfaces call.

Dependency direction: surfaces -> api -> services. Nothing here parses argv or renders
for a wire; it answers one question -- what did the old workflow leave behind, and, with
`apply`, which of those is safe to remove now.
"""

from __future__ import annotations

from pathlib import Path

from ..core import outcome as O
from ..services import onboard as ON


def preflight(repo: Path, *, apply: bool = False) -> O.Outcome:
    """Report leftover worktrees, branches and stashes; `apply` removes the merged ones.

    The report IS the product when `apply` is false: an unmerged branch is not ours to
    delete, and the operator decides whether to land it, import it (`ddflow_import`
    proposes unmerged branches) or leave it. With `apply`, only merged-and-clean
    worktrees (and the branch they are on) and merged branches are removed; a stale
    harness lock is unlocked first, a live one is never touched, and stashes are never
    dropped here.
    """
    leftovers = ON.preflight(repo)
    data = {
        "leftovers": [item.to_dict() for item in leftovers],
        "text": ON.render(leftovers),
    }
    if not leftovers:
        return O.nothing("onboard.preflight", ON.render(leftovers), **data)
    if not apply:
        return O.ok("onboard.preflight", **data)
    removed = ON.apply(repo)
    remaining = ON.preflight(repo)
    return O.ok(
        "onboard.preflight",
        removed=removed,
        remaining=[item.to_dict() for item in remaining],
        **data,
    )

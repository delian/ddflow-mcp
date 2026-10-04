"""Onboarding preflight, in the layer the surfaces call.

Dependency direction: surfaces -> api -> services. Nothing here parses argv or renders
for a wire; it answers one question -- what did the old workflow leave behind, and, when
asked, which of those is safe to remove now.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ..core import outcome as O
from ..services import onboard as ON


def preflight(repo: Path, *, apply: bool = False, only: Sequence[str] = ()) -> O.Outcome:
    """Report leftover worktrees, branches and stashes; `apply` removes the merged ones.

    The report IS the product when `apply` is false: an unmerged branch is not ours to
    delete, and the operator decides whether to land it, import it (`ddflow_import`
    proposes unmerged branches) or leave it. With `apply`, `only` is the operator's
    approval: empty removes everything the report marked `remove`; a list removes
    exactly those names (a name not marked remove is refused, never acted on). A
    removal that failed is a FAILED outcome, not a success line to parse.
    """
    leftovers = ON.preflight(repo)
    data = {
        "leftovers": [item.to_dict() for item in leftovers],
        "text": ON.render(leftovers),
    }
    if not leftovers:
        return O.nothing("onboard.preflight", str(data["text"]), **data)
    if not apply:
        return O.ok("onboard.preflight", **data)
    results = ON.apply(repo, list(only) if only else None)
    removed = [r for r in results if r["outcome"] == "removed"]
    refused = [r for r in results if r["outcome"] == "refused"]
    failed = [r for r in results if r["outcome"] == "failed"]
    remaining = [item.to_dict() for item in ON.preflight(repo)]
    if failed:
        return O.failed(
            "onboard.preflight",
            "; ".join(str(r.get("detail", r["name"])) for r in failed),
            **data,
            removed=removed,
            refused=refused,
            failed=failed,
            remaining=remaining,
        )
    return O.ok(
        "onboard.preflight",
        **data,
        removed=removed,
        refused=refused,
        failed=[],
        remaining=remaining,
    )

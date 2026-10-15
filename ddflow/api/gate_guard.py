"""Refusals shared by every verb that records a gate outcome (B8b2dafcbf3).

A finished item is not a place to record a gate: a stray `failed` on a completed item
reads as a regression nobody can explain. And a review of item A run from item B's tree
reviews the wrong diff. Both are refused, naming the way through; ``--force --reason``
overrides the first and is recorded.
"""

from __future__ import annotations

from pathlib import Path

from ..core.model import DONE
from ..infra import worktree as W
from ..services import leases as L


def closed_item_refusal(it, *, force: bool, reason: str, what: str) -> tuple[str, str]:
    """(refusal, forced-by): ("", "") for an open item; (text, "") to refuse a COMPLETE or
    abandoned one; ("", note) when ``--force`` with a reason overrides, ``note`` recorded."""
    if it is None or not it.terminal:
        return "", ""
    state = "complete" if it.state == DONE else "abandoned"
    base = (
        f"{it.id} is {state}: {what} would write onto a finished item (its recorded gates "
        f"are what it was completed on). "
    )
    if not force:
        return base + "To record on it anyway: pass --force --reason '<why>'.", ""
    if not (reason or "").strip():
        return base + "--force needs --reason '<why>' (recorded).", ""
    return "", f"forced onto a {state} item: {reason.strip()}"


def wrong_tree_refusal(repo: Path, st, it, called_from: Path | None) -> str:
    """Why ``it`` cannot be reviewed from where the caller stands, or "".

    Refused: standing in a linked worktree that is another item's (any state), or one that
    is not ``it``'s own tree when ``it`` has one. The primary checkout is allowed: it is
    nobody's tree, and the item's own tree is what gets diffed."""
    here = W.current(called_from or repo)
    if here is None:
        return ""
    stored = W.store_path(repo, here.path)
    own = W.load_path(repo, it.worktree) if it.worktree else None
    if own is not None and own.exists():
        if W.store_path(repo, own) == stored:
            return ""
        return (
            f"you are in {here.path}, not {it.id}'s worktree: run the review from {own} "
            f"(or from the primary checkout)."
        )
    for other in st.items.values():
        if other.id != it.id and not other.removed and (other.worktree or "") == stored:
            return (
                f"{here.path} is {other.id}'s worktree, not {it.id}'s: run {it.id}'s "
                f"review from its own tree, or pass --branch <branch>."
            )
    return ""


def guard_record(log, cfg, it, *, force: bool, reason: str, what: str) -> tuple[str, str]:
    """(refusal, forced-by) for recording or running a gate on ``it``: a finished item, then
    another clone's live lease. Either may be overridden with ``--force --reason``."""
    shut, note = closed_item_refusal(it, force=force, reason=reason, what=what)
    if shut:
        return shut, ""
    refusal, forced = L.foreign_guard(log, cfg, it.id, force=force, reason=reason, what=what)
    return refusal, forced or note

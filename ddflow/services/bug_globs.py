"""The files a fix task's bugs declared (`bug found --globs`)."""

from __future__ import annotations

from ..config import Config
from ..core.model import State
from .completion import fixes_of


def bug_globs_of(state: State, item_id: str, cfg: Config | None = None) -> list[str]:
    """The files the bugs ``item_id`` fixes declared (`bug found --globs`), in bug order:
    what a claim of a fix task with none of its own covers, so the commit hook does not
    refuse the very fix the bug names."""
    out: list[str] = []
    for b in sorted(fixes_of(state, item_id, cfg)):
        out += [g for g in state.bugs[b].globs if g not in out]
    return out

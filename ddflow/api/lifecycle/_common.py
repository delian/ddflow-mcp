"""What every lifecycle module shares: requiring an item to exist.

Part of `ddflow.api.lifecycle`, which re-exports the public names defined here."""

from __future__ import annotations

from ...core import outcome as O
from ...services.cleanup import stranded_text


def _require(st, item: str, kind: str):
    it = st.items.get(item)
    if it is None or it.removed:
        if it is not None and it.lease:
            # A holder's branch is still out there (B71dedb76d2): say so, not "no such item".
            ls = it.lease
            return O.refused(
                kind, stranded_text(item, ls.holder, ls.branch or it.branch, ls.worktree), id=item
            )
        gone = " (it was removed from the queue)" if it is not None else ""
        return O.failed(kind, f"no such item {item!r}{gone}", id=item)
    return it

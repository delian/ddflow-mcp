"""What every lifecycle module shares: requiring an item to exist.

Part of `ddflow.api.lifecycle`, which re-exports the public names defined here."""

from __future__ import annotations

import time

from ...core import outcome as O
from ...services.cleanup import stranded_text


def _require(st, item: str, kind: str, grace_s: int, mine: str | None = None):
    it = st.items.get(item)
    if it is None or it.removed:
        if it is not None and it.lease and it.lease.live(time.time(), grace_s):
            # A LIVE lease only (a lapsed one strands nothing): its holder's branch is still
            # out there (B71dedb76d2), so say so, not "no such item".
            ls = it.lease
            text = stranded_text(
                item, ls.holder, ls.branch or it.branch, ls.worktree, ls.clone, mine
            )
            return O.refused(kind, text, id=item)
        gone = " (it was removed from the queue)" if it is not None else ""
        return O.failed(kind, f"no such item {item!r}{gone}", id=item)
    return it

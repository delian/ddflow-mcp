"""What every lifecycle module shares: requiring an item to exist.

Part of `ddflow.api.lifecycle`, which re-exports the public names defined here."""

from __future__ import annotations

import time

from ...config import Config
from ...core import outcome as O
from ...services.cleanup import stranded_text

#: The shipped `[lease].grace_s`: callers that hold no config (`merge`, `complete` are
#: leased to others while this lands) judge a lease live by the default.
_GRACE = Config().lease.grace_s


def _require(st, item: str, kind: str, grace_s: int | None = None):
    it = st.items.get(item)
    if it is None or it.removed:
        if (
            it is not None
            and it.lease
            and it.lease.live(time.time(), _GRACE if grace_s is None else grace_s)
        ):
            # A LIVE lease only: a lapsed one strands nothing. A holder's branch is still out there (B71dedb76d2): say so, not "no such item".
            ls = it.lease
            return O.refused(
                kind, stranded_text(item, ls.holder, ls.branch or it.branch, ls.worktree), id=item
            )
        gone = " (it was removed from the queue)" if it is not None else ""
        return O.failed(kind, f"no such item {item!r}{gone}", id=item)
    return it

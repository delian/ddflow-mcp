"""What every lifecycle module shares: requiring an item to exist.

Part of `ddflow.api.lifecycle`, which re-exports every name defined here."""

from __future__ import annotations

from ...core import outcome as O


def _require(st, item: str, kind: str):
    it = st.items.get(item)
    if it is None or it.removed:
        gone = " (it was removed from the queue)" if it is not None else ""
        return O.failed(kind, f"no such item {item!r}{gone}", id=item)
    return it

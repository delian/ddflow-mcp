"""What every fold-handler module shares: the item an event is about. Pure."""

from __future__ import annotations

from ..events import Event
from ..records import Item, State


def _item(state: State, ev: Event, kind: str) -> Item:
    it = state.items.get(ev.subject)
    if it is None:
        it = Item(id=ev.subject, kind=kind, created_at=ev.ts)
        state.items[ev.subject] = it
    return it

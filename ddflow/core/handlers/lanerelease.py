"""The fold of `lane.released_for_gate`: an item was handed to the merge queue.

The event is a record for the log and `ddflow history`; the lease it ends is its own
`lease.released` event, so the fold has nothing to change. Pure."""

from __future__ import annotations

from ..events import Event
from ..records import State


def h_released_for_gate(st: State, ev: Event) -> None:
    """Nothing to fold: the release itself is the `lease.released` that follows."""


LANERELEASE_HANDLERS = {"lane.released_for_gate": h_released_for_gate}

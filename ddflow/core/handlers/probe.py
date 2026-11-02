"""The fold of `probe.recorded`: a conflict probe was run (`ddflow probe`).

A record for the log, `ddflow history` and the callers that read the newest probe of an
item (release at merge-ready, suggestions, peer notices); it changes no state. Pure."""

from __future__ import annotations

from ..events import Event
from ..records import State


def h_probe_recorded(st: State, ev: Event) -> None:
    """Nothing to fold: the event is read back from the log, not from `State`."""


PROBE_HANDLERS = {"probe.recorded": h_probe_recorded}

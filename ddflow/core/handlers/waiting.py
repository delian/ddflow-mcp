"""The fold of `lease.waiting` into `State.waiting`: who was refused which files, and by whom.

An agent refused the files it asked for (`claim`, or `update --globs` on a claimed item)
leaves this record and returns at once instead of polling while it holds its old files.
The record is the edge set of the wait-for graph (`services.waiting.wait_for_graph`).

The fold only keeps the NEWEST record per item; whether it still stands (the waiter's lease
live and unchanged, the wanted files not yet its own, the record's TTL) depends on the clock
and is asked at read time, so a replay folds the same state whenever it runs. Pure."""

from __future__ import annotations

from typing import Any

from ..events import Event
from ..records import State


def _row(ev: Event) -> dict[str, Any]:
    d = ev.data
    return {
        "holder": str(d.get("holder") or ev.agent),
        "globs": [str(g) for g in d.get("globs", [])],
        "on": [dict(o) for o in d.get("on", []) if isinstance(o, dict)],
        "since": float(d.get("since", d.get("at", 0.0))),
        "queue": bool(d.get("queue")),
        "ttl_s": int(d.get("ttl_s", 0)),
        "lease_event": str(d.get("lease_event", "")),
        "agent": ev.agent,
        "at": float(d.get("at", 0.0)),
    }


def h_waiting(st: State, ev: Event) -> None:
    """The waiter's newest refusal replaces its older one (one waiting record per item)."""
    if ev.subject:
        st.waiting[ev.subject] = _row(ev)


#: event kind -> its handler, for `model.HANDLERS`.
WAITING_HANDLERS = {"lease.waiting": h_waiting}

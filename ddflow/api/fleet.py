"""`ddflow lanes fleet` and `lanes resume <agent>`: the fleet registry views, read-only."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..core import outcome as O
from ..core.events import Event
from ..core.records import State
from ..services import fleet as FL


def fleet_view(
    data: dict[str, Any],
    *,
    view: str,
    lane: str,
    events: Sequence[Event],
    st: State,
    cfg: Any,
    now: float,
    plan: Any,
) -> O.Outcome:
    """Fill ``data`` with the registry (``fleet``) or one resume (``resume``)."""
    lanes = FL.ledger(events, st, cfg, now, plan)
    entries = FL.registry(events, lanes, st, cfg, now)
    data["lanes"] = []  # the window's ledger is the state view's: these views answer alone
    if view == "fleet":
        cap = cfg.lanes.fleet_max_rows
        shown = entries[:cap] if cap > 0 else entries
        data["fleet"] = [e.as_dict(now) for e in shown]
        data["fleet_total"] = len(entries)
        data["text"] = FL.render(shown, now, len(entries))
        if not entries:
            return O.nothing("lanes", "no agents seen", **data)
        return O.ok("lanes", **data)
    if not lane:
        return O.failed(
            "lanes", "resume needs an agent name: `ddflow lanes resume <agent>`", **data
        )
    res = FL.resume(entries, lane, st, now, cfg.lease.grace_s)
    data["resume"] = res
    data["text"] = res["reason"] + (f"\nrun: {res['command']}" if res["command"] else "")
    if not res["eligible"]:
        return O.nothing("lanes", res["reason"], **data)
    return O.ok("lanes", **data)

"""`ddflow lanes`: the lane ledger, a lane's brief and the advisor, read-only.

Nothing here writes an event, starts a process or opens a socket; the three views are
projections of the log as read now.
"""

from __future__ import annotations

import time
from pathlib import Path

from ..core import outcome as O
from ..core.schedule import plan
from ..services import flowstate as FL
from ..services import lanes as LN
from ._base import _load

VIEWS = ("state", "brief", "advise")
#: The agent the plan is computed for: nobody, so every live lease is another lane's.
NOBODY = "\0lanes"


def lanes(
    repo: Path, *, view: str = "state", lane: str = "", now: float | None = None, agent: str = ""
) -> O.Outcome:
    """The ledger (``state``), one lane's first prompt (``brief``) or the moves (``advise``)."""
    view = view or "state"
    if view not in VIEWS:
        return O.failed("lanes", f"unknown view {view!r}: one of {', '.join(VIEWS)}")
    log, cfg, st = _load(repo, agent)
    at = time.time() if now is None else now
    limit = FL.limit_for(Path(repo), cfg, st, log.read_all)
    events = log.read_all()
    ledger = LN.build(events, st, cfg, at, plan(st, cfg, now=at, agent=NOBODY, parallel=limit))
    # Every field of the wire shape is always set, so a "nothing" answer is the same body
    # on both surfaces (no null-padded lead).
    data: dict = {
        "view": view,
        "now": at,
        "lanes": [ln.as_dict(at) for ln in ledger],
        "lane": "",
        "brief": "",
        "moves": [],
        "limit": {},
        "text": "",
    }
    if view == "brief":
        found = LN.find(ledger, lane) if lane else None
        if found is None:
            names = ", ".join(ln.name for ln in ledger) or "none active"
            return O.failed("lanes", f"no lane {lane!r} (or not one): lanes are {names}", **data)
        brief = LN.lane_brief(found, st, cfg, ledger)
        return O.ok("lanes", **{**data, "lane": found.name, "brief": brief})
    if view == "advise":
        moves = LN.advise(ledger, limit, max_moves=cfg.lanes.advise_max)
        data["moves"] = [m.as_dict() for m in moves]
        data["text"] = LN.render_advice(moves)
        data["limit"] = {"limit": limit.limit, "mode": limit.mode, "reason": limit.reason}
        if not moves:
            return O.nothing("lanes", "no lane needs a move", **data)
        return O.ok("lanes", **data)
    data["text"] = LN.render_ledger(ledger, at)
    if not ledger:
        return O.nothing("lanes", "no lanes active in the window", **data)
    return O.ok("lanes", **data)

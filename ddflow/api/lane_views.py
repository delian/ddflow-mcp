"""`ddflow lanes`: the lane ledger, a lane's brief and the advisor; `lanes approve` a person's act.

The state/brief/advise/fleet/resume views are read-only: nothing in them writes an event,
starts a process or opens a socket. `lane_template_approve` (view ``approve``) is the one
write -- a PERSON approving a lane template -- which records an approval event and nothing else.
"""

from __future__ import annotations

import time
from pathlib import Path

from ..core import outcome as O
from ..services import approval as AP
from ..services import fleet as FR
from ..services import flowstate as FL
from ..services import lanes as LN
from ..services.lanes import launch_template as T
from ..services.lanes.redraw import watch as _watch
from ._base import _load
from .fleet import fleet_view
from .lifecycle.planning import plan_for
from .reporting.waitcycles import wait_moves, wait_report

VIEWS = ("state", "brief", "advise", "fleet", "resume", "approve")
#: The agent the plan is computed for: nobody, so every live lease is another lane's.
NOBODY = "\0lanes"


def lane_template_approve(
    repo: Path,
    name: str,
    cfg,
    log,
    st,
    *,
    agent: str = "",
) -> O.Outcome:
    """`lanes approve <name>`: a PERSON approves the lane template as it is now (B8647b69e06).

    The only writer of `approval.granted` under the subject ``lane-template:<name>``; until this
    command was wired, `launch_template.approve` had no caller and `approved()` could never pass
    outside a test. Refused under an agent identity."""
    if not name:
        return O.failed("lanes", "a template name is required: `ddflow lanes approve <name>`")
    templates, notes = T.load(Path(repo) / cfg.swarm.launch_templates_file)
    t = templates.get(name)
    if t is None:
        known = ", ".join(sorted(templates)) or "none"
        return O.failed("lanes", f"no template {name!r} (templates: {known})")
    try:
        granted = T.approve(log, t, requested_agent=agent)
    except AP.ApprovalRefused as exc:  # a person's act: refused under an agent identity
        return O.refused("lanes.approve", str(exc))
    return O.ok(
        "lanes.approve",
        name=name,
        digest=t.digest(),
        approved_by=granted.actor.user,
        text=f"template {name!r} approved at digest {t.digest()} by {granted.actor.user}",
    )


def lanes(
    repo: Path, *, view: str = "state", lane: str = "", now: float | None = None, agent: str = ""
) -> O.Outcome:
    """The ledger (``state``), one lane's first prompt (``brief``) or the moves (``advise``)."""
    view = view or "state"
    if view not in VIEWS:
        return O.failed("lanes", f"unknown view {view!r}: one of {', '.join(VIEWS)}")
    log, cfg, st = _load(repo, agent)
    if view == "approve":
        return lane_template_approve(Path(repo), lane, cfg, log, st, agent=agent)
    at = time.time() if now is None else now
    limit = FL.limit_for(Path(repo), cfg, st, log.read_all)
    events = log.read_all()
    offer = plan_for(Path(repo), log, cfg, st, purpose="view", agent=NOBODY, now=at, events=events)
    ledger = LN.build(events, st, cfg, at, offer)
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
        "fleet": [],
        "fleet_total": 0,
        "resume": {},
    }
    if view in ("fleet", "resume"):
        return fleet_view(
            data, view=view, lane=lane, events=events, st=st, cfg=cfg, now=at, plan=offer
        )
    if view == "brief":
        found = LN.find(ledger, lane) if lane else None
        if found is None and lane:  # `lanes resume` offers this for an agent past the window
            found = LN.find(FR.ledger(events, st, cfg, at, offer), lane)
        if found is None:
            names = ", ".join(LN.safe(ln.name) for ln in ledger) or "none active"
            return O.failed(
                "lanes", f"no lane {LN.safe(lane)!r} (or not one): lanes are {names}", **data
            )
        brief = LN.lane_brief(found, st, cfg, ledger)
        return O.ok("lanes", **{**data, "lane": found.name, "brief": brief})
    if view == "advise":
        cap = cfg.lanes.advise_max
        cycles, alarms = wait_report(log, st, cfg, events, at)
        moves = wait_moves(LN.advise(ledger, limit, max_moves=cap), cycles, alarms, cap)
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


def lanes_watch(draw, mark, *, interval_s: float) -> int:
    """Redraw the ledger when the log changes or every ``interval_s``, until interrupted.

    The CLI's `--watch`: a foreground loop over the cheap "did the log grow" question; it
    starts no thread and no daemon and spawns nothing.
    """
    return _watch(draw, mark, interval_s=interval_s)

"""`ddflow dispatch [--plan|--tick]` and MCP `ddflow_dispatch`: what to start now. Read.

DX-dispatch-plan. The read half of the planner: it reads the queue (`ddflow next`), the
fleet (lanes + templates + catalog) and the scheduler world, then asks the pure planner
(`services.dispatch.plan`) which items to start right now, in order, on which lane. Advise
only -- nothing starts, nothing is claimed, nothing writes, unless `--tick` records the plan
as one `dispatch.planned` event so the advice is auditable.

Read-only by construction (r-read-verbs-read-only): the item act is still `ddflow claim`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..adapters.clock import DEFAULT as CLOCK
from ..core import outcome as O
from ..core.schedule import caps
from ..services import dispatch as PLAN
from ..services import flowstate as FL
from ..services.lanes import launch_template as T
from ..services.swarm import dispatch as D
from ..services.swarm import score
from ..services.swarm.score_config import Weights
from ._base import _load
from .lifecycle.planning import plan_for
from .lifecycle.pull import _source as _lane_source
from .lifecycle.pull import _world_and_items

#: The two modes; `--tick` is `--plan` plus one auditable `dispatch.planned` event.
MODES = ("plan", "tick")


def dispatch(
    repo: Path,
    *,
    mode: str = "plan",
    phase: str = "",
    agent: str = "",
    phase_only: bool = False,
) -> O.Outcome:
    """The plan. ``mode`` is ``plan`` (advise) or ``tick`` (advise + one event)."""
    if mode not in MODES:
        return O.failed("dispatch", f"unknown mode {mode!r}: one of {', '.join(MODES)}")
    log, cfg, st = _load(repo, agent)
    now = CLOCK.wall()
    me = cfg.agent.id or log.agent_id
    p = plan_for(
        repo,
        log,
        cfg,
        st,
        purpose="offer",
        phase=phase,
        agent=me,
        phase_only=phase_only,
        now=now,
    )
    parallel = FL.limit_for(repo, cfg, st, log.read_all)
    live = st.active_leases(now, cfg.lease.grace_s)
    c = caps(st, cfg, live, parallel)  # the two caps in force, counted across the queue
    free = max(0, min(c.flight_slots, c.tree_slots))
    ids = [i.id for i in p.ready]

    w = Weights.from_config(cfg)
    templates, _notes = T.load(Path(repo) / cfg.swarm.launch_templates_file)
    lanes: list[score.Lane] = []
    for name in sorted(templates):
        src = _lane_source(repo, cfg, st, name, now)
        if D.gaps(src):
            continue
        lanes.append(D.lane_of(name, src))

    world, items = _world_and_items(log.read_all(), cfg, st, ids, phase, now)
    starts, _ = PLAN.fill(items, lanes, world, free, w)

    offer = plan_for(repo, log, cfg, st, purpose="view", agent=me, now=now)
    events = log.read_all()
    from ..services import lanes as LN
    from ..services.lanes import advise as AD

    ledger = LN.build(events, st, cfg, now, offer)
    moves = PLAN.steer(AD(ledger, parallel, max_moves=cfg.lanes.advise_max))

    rows = [
        {"item": s.item, "lane": s.lane, "globs": list(s.globs), "note": s.note}
        for s in starts
    ]
    data: dict[str, Any] = {
        "mode": mode,
        "free": free,
        "limit": parallel.limit,
        "starts": rows,
        "moves": [{"verb": m.verb, "lane": m.lane, "text": m.text} for m in moves],
        "text": _render(rows, moves),
    }
    if mode == "tick":
        log.append(
            "session.note",
            me or "swarm",
            {"text": _render(rows, moves), "source": "dispatch"},
        )
        data["text"] = _render(rows, moves)
    return O.ok("dispatch", **data)


def _render(rows: list[dict], moves: list[dict]) -> str:
    """The human form: one line per start, in order, then the lanes to steer."""
    lines = [
        f"{i + 1}. {r['item']} -> {r['lane']}" + (f"  ({r['note']})" if r["note"] else "")
        for i, r in enumerate(rows)
    ]
    if moves:
        lines.append("steer " + "; ".join(f"{m['verb']} {m['lane']}" for m in moves))
    return "\n".join(lines) if lines else "nothing to start now"

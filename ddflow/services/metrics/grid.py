"""The hourly-grid sampler: the plan as it stood at each of a series of past instants.

For each instant ``t`` the log up to ``t`` is folded and ``schedule.plan`` asked what was
ready, running and held back. Used by lane utilisation here and by ``parallel headroom``
(B-af-headroom). A fold is the expensive step, so the grid is capped at ``[metrics]
grid_max_folds`` instants ending now, ``[metrics] grid_step_s`` apart, and the answer says
when it sampled and whether the cap cut the window short.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Sequence

from ...config import Config
from ...core import progress as PR
from ...core.events import Event
from ...core.model import fold
from ...core.schedule import plan

#: The agent the plan is asked for: nobody, so every live lease is another lane's.
NOBODY = "\0metrics"


def instants(
    first: float, now: float, step_s: float, max_folds: int, since: float | None
) -> tuple[list[float], bool]:
    """(instants oldest first, whether the cap cut the window short): ``now`` and every
    ``step_s`` before it, back to ``since`` (or the first event), at most ``max_folds``."""
    start = max(first, since) if since is not None else first
    step = float(step_s)
    count = int((now - start) // step) + 1
    capped = count > max_folds
    count = max(min(count, max(int(max_folds), 1)), 1)
    return [now - i * step for i in reversed(range(count))], capped


def sample(events: Sequence[Event], cfg: Config, now: float, since: float | None = None) -> dict:
    """Rows of ``{at, in_flight, ready, blocked, conflict, lanes, working}`` and how they
    were sampled. ``lanes`` are the agents with a lease event in the step before the instant,
    (a lease event is what makes an agent a lane) plus every holder of a live lease; ``working`` the holders of live leases."""
    stamped = sorted(((PR.epoch(e.ts), e) for e in events), key=lambda p: (p[0], p[1].lamport))
    stamped = [p for p in stamped if p[0] > 0]
    m = cfg.metrics
    step = max(float(m.grid_step_s), 1.0)  # the one clamp: spacing, lookback and the report
    if not stamped:
        return {"rows": [], "step_s": step, "capped": False, "folds": 0}
    times = [t for t, _ in stamped]
    ats, capped = instants(times[0], now, step, int(m.grid_max_folds), since)
    rows = []
    for t in ats:
        upto = bisect_right(times, t)
        st = fold([e for _, e in stamped[:upto]], strict=False)
        offer = plan(st, cfg, now=t, agent=NOBODY)
        working = {it.lease.holder for it in st.items.values() if it.lease and it.lease.live(t)}
        recent = {
            e.agent
            for _, e in stamped[bisect_left(times, t - step) : upto]
            if e.kind.startswith("lease.")
        }
        lanes = recent | working
        rows.append(
            {
                "at": t,
                "in_flight": len(offer.running),
                "ready": len(offer.ready),
                "blocked": len(offer.blocked),
                "conflict": len(offer.overlapped),
                "lanes": len(lanes),
                "working": len(working),
            }
        )
    return {"rows": rows, "step_s": step, "capped": capped, "folds": len(rows)}


def utilisation(rows: Sequence[dict]) -> dict:
    """Working lane-samples over working plus idle-beside-ready ones. None when no instant
    had a lane at all: a project nobody worked on has no utilisation, not 0%."""
    working = sum(r["working"] for r in rows)
    idle_ready = sum(min(max(r["lanes"] - r["working"], 0), r["ready"]) for r in rows)
    whole = working + idle_ready
    return {
        "samples": len(rows),
        "working": working,
        "idle_with_ready": idle_ready,
        "utilisation": working / whole if whole else None,
    }

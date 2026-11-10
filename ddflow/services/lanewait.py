"""Lane-hours spent waiting on shared gates, and the lane-wait before/after figures.

`LC-measure` owns these numbers. They read the event log and the ``ddflow metrics lanes``
grid (the ``SW-baseline`` method), so they stay comparable to that baseline; there is no
second collector and no daemon. Pure: ``now`` is an argument, never a clock read, so a test
freezes it. Every figure that cannot be computed is ``None`` (never a zero standing in for
one) and named under ``unavailable``.

Conformance (r-reuse-core, r-bounded-everything): merged-per-hour is ``swarm_metrics.baseline``
(the ``SW-baseline`` method) and the queue wait, the gate-lock wait and the grid are the ones
``services.metrics`` already derives; this module only aggregates them, so the numbers and
their caps come from one place.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..config import Config
from ..core.events import Event
from ..core.model import State
from . import swarm_metrics as SW
from .metrics import facts as FX
from .metrics import grid as GRID
from .metrics import summary as SUM
from .metrics.providers import Context
from .metrics.stats import stat


def context(
    events: Sequence[Event],
    st: State,
    cfg: Config,
    now: float,
    since: float | None = None,
) -> Context:
    """The one folded context the queue and gate figures read, mirroring the other views."""
    return Context(FX.collect(events, st, now, cfg), cfg, now, since)


def lane_wait(
    events: Sequence[Event],
    st: State,
    cfg: Config,
    now: float,
    since: float | None = None,
) -> dict:
    """The lane-wait figures as plain data, repo-wide (like ``ddflow metrics lanes``):

    - ``gate_wait_hours``: ``{"total": ...}`` plus ``"by_gate"`` -> lane-hours queued for a
      shared host lock, per gate. Every value is a shared-lock wait by construction:
      ``waited_s`` records seconds queued for the host lock, so a gate with no such wait has
      no entry (never a zero), and the total sits under ``total``, apart from gate names.
    - ``ready_to_claimed``: queue wait (ready to first claimed), p50/p90/max.
    - ``ci_lock_queue``: the ``ci`` gate's queued run, p50/p90/max.
    - ``idle``: lane utilisation from the grid (``SW-baseline``): lane-samples working vs
      idle beside ready work, and the utilisation share.
    - ``merged``: merged items in the window and per hour, from ``swarm_metrics.baseline``.
    """
    ctx = context(events, st, cfg, now, since)
    minimum = int(cfg.metrics.min_samples)
    unavailable: dict[str, str] = {}

    per_gate = SUM.gate_waits(ctx)
    gate_wait_hours = {
        gate: sum(seconds) / 3600.0 for gate, seconds in per_gate.items() if seconds
    }

    ready = stat(SUM.queue_waits(ctx), minimum)
    if ready["n"] < max(minimum, 1):
        unavailable["ready_to_claimed"] = (
            f"{ready['n']} items with a dated wait, need {minimum}"
        )

    ci = stat(per_gate.get("ci", []), minimum)
    if ci["n"] < max(minimum, 1):
        unavailable["ci_lock_queue"] = (
            f"{ci['n']} ci runs with a waited_s, need {minimum}"
        )

    grid = GRID.sample(events, cfg, now, since)
    util = GRID.utilisation(grid["rows"])
    if util["utilisation"] is None:
        unavailable["idle"] = "no lane worked in the sampled window"

    base = SW.baseline(events, st, cfg, now, since=since)
    if base["throughput"]["per_hour"] is None:
        unavailable["merged_per_hour"] = base["unavailable"].get(
            "throughput", "no elapsed time in the window"
        )

    return {
        "min_samples": minimum,
        "gate_wait_hours": {
            "total": sum(gate_wait_hours.values()),
            "by_gate": gate_wait_hours,
        },
        "ready_to_claimed": ready,
        "ci_lock_queue": ci,
        "idle": {
            "samples": util["samples"],
            "working_lane_samples": util["working"],
            "idle_with_ready_lane_samples": util["idle_with_ready"],
            "utilisation": util["utilisation"],
        },
        "merged": {"merges": base["merged"], "per_hour": base["throughput"]["per_hour"]},
        "unavailable": unavailable,
    }

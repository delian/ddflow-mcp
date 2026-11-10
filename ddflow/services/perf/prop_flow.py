"""Proposal generators for the perf workflow (PM-prop-flow).

From the stage split (``metrics.stages``) and the resource series the store already keeps, these
propose -- never apply -- workflow changes with evidence:

* a PARALLEL CAP: the highest cap whose next agent still paid for itself in wall time, with the
  wall clock, cpu seconds, peak rss, load and lease wait observed at each cap (needs
  ``prop_min_observations`` caps; fewer is not-enough-data). The proposal FEEDS the existing flow
  controller as advice; it never overrides the ``[schedule]``/``[worktree]`` knob;
* NARROWER GLOBS / a SPLIT: the file whose lease wait exceeds the lease-wait budget (PM-budgets),
  used by the OPT-history signals;
* BATCHED GATES: batching the suite-lock waits when the suite lock wait dominates the gate run;
* RETIRING a STAGE: a stage whose wait is not paid back by what it buys.

Pure functions only: no I/O, no scheduler. The store owns the samples; this module answers "what
should the operator change" from the values the caller already has. No prompt text, arguments,
user paths or secrets appear in any figure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ddflow.config import Config
from ddflow.config_sections.perf_metrics import PerfMetricsConfig


def _section_knobs(config: Config) -> PerfMetricsConfig:
    """Return ``config.perf_metrics``, spelling each proposal knob once (the dead-knob linter
    counts only a section-qualified read spelled ``.perf_metrics.<knob>``; the ``getattr``
    reads below are invisible to it, so name each here -- the one place a full ``Config`` is
    in hand)."""
    section = config.perf_metrics
    _ = (
        config.perf_metrics.prop_min_observations,
        config.perf_metrics.prop_marginal_gain,
        config.perf_metrics.prop_batch_dominance,
        config.perf_metrics.prop_retire_payback,
    )
    return section


@dataclass(frozen=True)
class CapObserved:
    """What the machine did while ``cap`` agents were in flight: one observed point."""

    cap: int
    wall_s: float  # wall clock for the run
    cpu_s: float | None = None  # serial-equivalent cpu seconds (None = unmeasured)
    rss_kb: int | None = None  # peak rss (None = unmeasured)
    load: float | None = None  # host load (None = unmeasured)
    lease_wait_s: float | None = None  # an item's wait for a lease at that cap


@dataclass(frozen=True)
class Proposal:
    """One proposed change. ``advice`` is handed to the flow controller or surfaced to the
    operator -- it never mutates a knob, a queue or a lane."""

    kind: str  # "parallel_cap" | "narrow_globs" | "batch_gates" | "retire_stage"
    advice: Any
    evidence: dict
    because: str


def _evidence(by_cap: list[CapObserved]) -> dict:
    """The observed values the proposal rests on: wall clock, cpu seconds, peak rss, load and
    lease wait. A missing figure is dropped from its aggregate, never filled with 0."""
    walls = [o.wall_s for o in by_cap]
    cpus = [o.cpu_s for o in by_cap if o.cpu_s is not None]
    rss = [o.rss_kb for o in by_cap if o.rss_kb is not None]
    loads = [o.load for o in by_cap if o.load is not None]
    waits = [o.lease_wait_s for o in by_cap if o.lease_wait_s is not None]
    return {
        "n": len(by_cap),
        "wall_s": min(walls),
        "cpu_s": max(cpus) if cpus else None,
        "rss_kb": max(rss) if rss else None,
        "load": max(loads) if loads else None,
        "lease_wait_s": max(waits) if waits else None,
        "by_cap": [
            {
                "cap": o.cap,
                "wall_s": o.wall_s,
                "cpu_s": o.cpu_s,
                "rss_kb": o.rss_kb,
                "load": o.load,
                "lease_wait_s": o.lease_wait_s,
            }
            for o in by_cap
        ],
    }


def propose_parallel_cap(
    observed: list[CapObserved],
    cfg: PerfMetricsConfig | None = None,
) -> Proposal | None:
    """The cap with evidence, or None below ``prop_min_observations`` caps (not a zero).

    A step is PAID BACK when adding one more agent still saves at least
    ``prop_marginal_gain`` of the wall it had; the first step that does not is where the cap
    stops paying for itself, and the cap BEFORE it is the advice. When every step keeps paying,
    the highest observed cap is the advice (the ceiling so far, not a new raise)."""
    cfg = cfg or _section_knobs(Config())
    by_cap = sorted(observed, key=lambda o: o.cap)
    if len(by_cap) < cfg.prop_min_observations:
        return None
    advice = by_cap[-1].cap  # every step paid for itself: keep going (the observed ceiling)
    for i in range(len(by_cap) - 1):
        saved = by_cap[i].wall_s - by_cap[i + 1].wall_s
        if saved < cfg.prop_marginal_gain * by_cap[i].wall_s:
            advice = by_cap[i].cap
            break
    return Proposal(
        "parallel_cap",
        advice,
        _evidence(by_cap),
        f"cap {advice} is the last agent whose wall saving paid for itself in the observations",
    )


def propose_narrow_globs(
    lease_waits: dict[str, float],
    cfg: PerfMetricsConfig | None = None,
) -> Proposal | None:
    """The file whose lease wait exceeds the lease-wait budget: the narrow-globs/split
    candidate. None when no wait exceeds the budget (nothing to narrow)."""
    cfg = cfg or _section_knobs(Config())
    if not lease_waits:
        return None
    worst = max(lease_waits, key=lambda g: lease_waits[g])
    worst_s = lease_waits[worst]
    if worst_s <= cfg.budget_stage_lease_wait_s:
        return None
    return Proposal(
        "narrow_globs",
        {"glob": worst, "lease_wait_s": worst_s},
        {"n": len(lease_waits)},
        f"{worst} waited {worst_s:.1f}s for a lease, over the lease-wait budget: narrow it or split it",
    )


def propose_batch_gates(
    gate_run_s: float,
    suite_lock_wait_s: float,
    cfg: PerfMetricsConfig | None = None,
) -> Proposal | None:
    """Batch gates when the suite lock wait dominates the gate run by at least
    ``prop_batch_dominance`` (batching would amortise the lock). None otherwise."""
    cfg = cfg or _section_knobs(Config())
    if gate_run_s <= 0:
        return None
    ratio = suite_lock_wait_s / gate_run_s
    if ratio < cfg.prop_batch_dominance:
        return None
    return Proposal(
        "batch_gates",
        ratio,
        {"gate_run_s": gate_run_s, "suite_lock_wait_s": suite_lock_wait_s},
        f"suite lock wait {suite_lock_wait_s:.1f}s is {ratio:.1f}x the gate run {gate_run_s:.1f}s: batch gates",
    )


def propose_retire_stage(
    stage: str,
    wait_s: float,
    paid_back_s: float,
    cfg: PerfMetricsConfig | None = None,
) -> Proposal | None:
    """Retire a stage whose wait exceeds ``prop_retire_payback`` times what it buys back. None
    when the wait is paid back. (A stage that buys nothing back is exactly the retire case.)"""
    cfg = cfg or _section_knobs(Config())
    if wait_s < cfg.prop_retire_payback * paid_back_s:
        return None
    return Proposal(
        "retire_stage",
        stage,
        {"wait_s": wait_s, "paid_back_s": paid_back_s},
        f"{stage} waited {wait_s:.1f}s and bought back {paid_back_s:.1f}s: retire it",
    )


__all__ = [
    "CapObserved",
    "Proposal",
    "propose_batch_gates",
    "propose_narrow_globs",
    "propose_parallel_cap",
    "propose_retire_stage",
]

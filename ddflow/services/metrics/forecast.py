"""``ddflow metrics forecast``: the critical-path ETA of a phase, as a range.

The floor of a phase's runtime is its longest chain of unfinished work (``schedule
.critical_path``). Each link takes as long as a completed item of its kind took, so the
chain's time is drawn by bootstrap from those observed durations (``[metrics]
forecast_runs`` draws, seeded by ``forecast_seed`` so the same log gives the same range)
and reported at two percentiles. A link already in flight has its elapsed time taken off
its draw. It is an ESTIMATE of the floor, with unlimited agents and no rework; under
``min_samples`` observed durations of a kind there is no range at all -- except a kind
with none of its own (a phase is never claimed, so a queue with no completed phase
falls back to the durations of completed work items and says so).
"""

from __future__ import annotations

import random
from collections.abc import Sequence

from ...config import Config
from ...core.model import State
from ...core.schedule import chain_cycles, critical_path
from .facts import Facts
from .stats import percentile


def draw_totals(
    pools: Sequence[Sequence[float]], elapsed: Sequence[float], runs: int, seed: int
) -> list[float]:
    """``runs`` simulated chain durations: per link, one observed duration drawn at random
    from its pool, less what the link has already run (never below zero)."""
    rng = random.Random(seed)
    return [
        sum(max(rng.choice(pool) - done, 0.0) for pool, done in zip(pools, elapsed, strict=True))
        for _ in range(runs)
    ]


def durations_by_kind(facts: Facts) -> dict[str, list[float]]:
    """One observed duration per completed item of each kind: claim to completion, and a
    phase's filed to completed (a phase is never claimed, so it has no cycle to observe,
    B153d9ac8ae)."""
    out: dict[str, list[float]] = {}
    for span in facts.spans.values():
        cycle = span.cycle_s
        if (
            cycle is None
            and span.kind == "phase"
            and span.added_at is not None
            and span.completed_at is not None
            and span.completed_at >= span.added_at
        ):
            cycle = span.completed_at - span.added_at
        if cycle is not None:
            out.setdefault(span.kind, []).append(cycle)
    return out


def _link_pools(
    pools_by_kind: dict[str, list[float]], kinds: Sequence[str], min_samples: int
) -> tuple[list[list[float]], dict[str, str], dict[str, int]]:
    """One pool PER LINK, in chain order, so pools stay aligned with the chain's elapsed
    times. Each link of a kind draws that kind's observed durations; a kind with none of
    its own (a phase before any phase has completed) draws the completed WORK durations
    instead, and ``fallback`` names every kind that happens for. ``samples`` (per
    distinct kind) counts what its links really drew."""
    work = [d for kind, ds in pools_by_kind.items() if kind != "phase" for d in ds]
    used, fallback = dict(pools_by_kind), {}
    for kind in dict.fromkeys(kinds):
        if not used.get(kind) and kind == "phase" and len(work) >= max(min_samples, 1):
            used[kind] = work
            fallback[kind] = "work"
    pools = [used.get(k) or [] for k in kinds]
    samples = {k: len(used.get(k, [])) for k in dict.fromkeys(kinds)}
    return pools, fallback, samples


def _empty_reason(state: State, phase: str, cfg: Config | None = None) -> str:
    """Why a phase has no critical path: a named cycle among open work, or no work."""
    cycles = chain_cycles(state, phase, cfg=cfg)
    if cycles:
        return (
            "the dependency graph has a cycle, so there is no longest chain: "
            + " -> ".join(cycles[0])
            + " (`ddflow plan check` names it)"
        )
    return "nothing unfinished: no critical path"


def _elapsed(facts: Facts, chain: Sequence[str], now: float) -> list[float]:
    """Seconds each link of the chain has already run (0 unless claimed and not finished)."""
    out = []
    for i in chain:
        span = facts.spans.get(i)
        held = span is not None and span.first_claim is not None and span.completed_at is None
        out.append(max(now - span.first_claim, 0.0) if held and span else 0.0)
    return out


def forecast(facts: Facts, state: State, cfg: Config, now: float, phase: str = "") -> dict:
    """The forecast as data. ``available`` False says why there is no range (``reason``)."""
    m = cfg.metrics
    base = {
        "estimate": True,
        "phase": phase,
        "available": False,
        "reason": "",
        "chain": [],
        "samples": {},
        "runs": int(m.forecast_runs),
        "low_pct": int(m.forecast_low_pct),
        "high_pct": int(m.forecast_high_pct),
        "low_s": None,
        "high_s": None,
        "fallback": {},
        "note": "an estimate of the floor: the longest unfinished chain, unlimited agents, no rework",
    }
    if phase and phase not in state.items:
        return {**base, "reason": f"no phase {phase!r}"}
    chain = critical_path(state, phase, cfg)
    if not chain:
        return {**base, "reason": _empty_reason(state, phase, cfg)}
    pools_by_kind = durations_by_kind(facts)
    kinds = [state.items[i].kind for i in chain]
    pools, fallback, samples = _link_pools(pools_by_kind, kinds, int(m.min_samples))
    base = {**base, "chain": chain, "samples": samples}
    if fallback:
        base["fallback"] = fallback
        base["note"] += " (no completed phase observed: the phase link draws work durations)"
    short = {k: n for k, n in samples.items() if n < int(m.min_samples)}
    if short:
        need = ", ".join(f"{k}: {n}" for k, n in short.items())
        reason = f"not enough data: completed items per kind ({need}), need {m.min_samples}"
        return {**base, "reason": reason}
    totals = draw_totals(
        pools,
        _elapsed(facts, chain, now),
        int(m.forecast_runs),
        int(m.forecast_seed),
    )
    low, high = percentile(totals, m.forecast_low_pct), percentile(totals, m.forecast_high_pct)
    return {**base, "available": True, "low_s": low, "high_s": high}

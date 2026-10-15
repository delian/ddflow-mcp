"""``ddflow metrics forecast``: the critical-path ETA of a phase, as a range.

The floor of a phase's runtime is its longest chain of unfinished work (``schedule
.critical_path``). Each link takes as long as a completed item of its kind took, so the
chain's time is drawn by bootstrap from those observed durations (``[metrics]
forecast_runs`` draws, seeded by ``forecast_seed`` so the same log gives the same range)
and reported at two percentiles. A link already in flight has its elapsed time taken off
its draw. It is an ESTIMATE of the floor, with unlimited agents and no rework; under
``min_samples`` observed durations of a kind there is no range at all.
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
    out: dict[str, list[float]] = {}
    for span in facts.spans.values():
        if span.cycle_s is not None:
            out.setdefault(span.kind, []).append(span.cycle_s)
    return out


def _empty_reason(state: State, phase: str) -> str:
    """Why a phase has no critical path: a named cycle among open work, or no work."""
    cycles = chain_cycles(state, phase)
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
        "note": "an estimate of the floor: the longest unfinished chain, unlimited agents, no rework",
    }
    if phase and phase not in state.items:
        return {**base, "reason": f"no phase {phase!r}"}
    chain = critical_path(state, phase)
    if not chain:
        return {**base, "reason": _empty_reason(state, phase)}
    pools_by_kind = durations_by_kind(facts)
    kinds = [state.items[i].kind for i in chain]
    samples = {k: len(pools_by_kind.get(k, [])) for k in dict.fromkeys(kinds)}
    base = {**base, "chain": chain, "samples": samples}
    short = {k: n for k, n in samples.items() if n < int(m.min_samples)}
    if short:
        need = ", ".join(f"{k}: {n}" for k, n in short.items())
        reason = f"not enough data: completed items per kind ({need}), need {m.min_samples}"
        return {**base, "reason": reason}
    totals = draw_totals(
        [pools_by_kind[k] for k in kinds],
        _elapsed(facts, chain, now),
        int(m.forecast_runs),
        int(m.forecast_seed),
    )
    low, high = percentile(totals, m.forecast_low_pct), percentile(totals, m.forecast_high_pct)
    return {**base, "available": True, "low_s": low, "high_s": high}

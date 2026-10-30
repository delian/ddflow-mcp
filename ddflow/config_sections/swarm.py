"""The `[swarm]` section: the weights and caps of the swarm scheduler core.

Re-exported from `ddflow.config`, which assembles `Config` from every section. Read by
`ddflow.services.swarm.score_config.Weights.from_config`."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ._docs import declare, knob


def _num(value: object) -> bool:
    """A real number the scorer can multiply as a float: finite once converted."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:  # an int too large for a float
        return False


def _nonneg(value: object) -> str:
    ok = _num(value) and value >= 0
    return "" if ok else "a number of zero or more"


def _share(value: object) -> str:
    ok = _num(value) and 0 <= value <= 1
    return "" if ok else "a number between 0 and 1"


def _positive(value: object) -> str:
    ok = _num(value) and value > 0
    return "" if ok else "a number above zero"


def _count(value: object) -> str:
    ok = isinstance(value, int) and not isinstance(value, bool) and value >= 0
    return "" if ok else "a whole number of zero or more"


@declare("swarm")
@dataclass
class SwarmConfig:
    """Scheduler core of the swarm: hard constraints first, then a weighted, reasoned score."""

    w_critical: float = knob(
        3.0,
        doc="Weight of the critical-path term in the dispatch score: an item on the longest dependency chain is offered before one with slack. Zero ignores the chain.",
        check=_nonneg,
    )
    w_fit: float = knob(
        2.0,
        doc="Weight of the fit term: the quality ledger's Beta-smoothed first-pass rate of the lane's (family, model) on the item's kind. An unmeasured pair scores the prior (0.5), not zero.",
        check=_nonneg,
    )
    w_cost: float = knob(
        1.0,
        doc="Weight of the cost term (cheaper estimated dollars score higher). Cost only ranks lanes whose fit is within `fit_margin` of the best eligible lane, so a free local route never beats a better-qualified one for being free.",
        check=_nonneg,
    )
    w_rework: float = knob(
        1.5,
        doc="Weight of the rework term: failed gates before completion plus bugs filed after it, per item, for the lane's pair on the item's kind. Lower rework scores higher.",
        check=_nonneg,
    )
    w_queue: float = knob(
        0.5,
        doc="Weight of the queue term: how long the item has waited, so a low-priority item is not starved.",
        check=_nonneg,
    )
    fit_margin: float = knob(
        0.10,
        doc="How far below the best eligible lane's fit a lane may be and still compete on cost. A lane further below is ranked after every lane inside the margin.",
        check=_share,
    )
    cost_ref_usd: float = knob(
        1.0,
        doc="Estimated dollars at which the cost term reaches zero (it falls linearly from a free route at one). Must be above zero.",
        check=lambda v: (
            ""
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0
            else "a number above zero"
        ),
    )
    rework_ref: float = knob(
        1.0,
        doc="Rework events per item at which the rework term reaches zero. Must be above zero.",
        check=lambda v: (
            ""
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0
            else "a number above zero"
        ),
    )
    queue_ref_s: int = knob(
        3600,
        doc="Seconds of waiting at which the queue term reaches one (it saturates there). Must be above zero.",
        check=_positive,
    )
    max_lanes: int = knob(
        4,
        doc="Most lanes holding an item at once across the swarm. Zero admits none.",
        check=_count,
    )
    family_cap: int = knob(
        3,
        doc="Most live claims one model family may hold at once, so one vendor's outage or rate limit cannot stall the swarm.",
        check=_count,
    )
    hedge_max: int = knob(
        1,
        doc="Extra concurrent attempts one item may have beyond the first (hedging, D-swarm-answers Q5). Zero turns hedging off.",
        check=_count,
    )
    default_min_trust: int = knob(
        0,
        doc="Trust tier a lane needs for an item that names none. A lane's tier is whatever the operator grants it; a lane below the item's tier is refused, whatever its score.",
        check=_count,
    )
    explore_share: float = knob(
        0.10,
        doc="Share of picks spent on under-measured (family, model) pairs (D-swarm-answers Q10). Zero turns exploration off.",
        check=_share,
    )
    explore_min_n: int = knob(
        5,
        doc="Items per (family, model, kind) under which a pair counts as under-measured and may be explored.",
        check=_count,
    )
    bug_window_s: int = knob(
        604800,
        doc="A bug filed this many seconds after an item completed still counts against the author in the quality ledger (default a week).",
        check=_count,
    )

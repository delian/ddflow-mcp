"""The scheduler's knobs as one frozen value, read from ``[swarm]`` (and the model catalog).

``Weights.from_config`` is the only reader of the ``[swarm]`` section for scoring, so
``score`` itself never sees a ``Config``. ``lane_prices`` reads a lane's price and window
from the model catalog (``modelreg.read``); an unverified value stays None and is reported
as such, never replaced by a guess.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...config import Config
from .. import modelreg


@dataclass(frozen=True)
class Weights:
    w_critical: float = 3.0
    w_fit: float = 2.0
    w_cost: float = 1.0
    w_rework: float = 1.5
    w_queue: float = 0.5
    fit_margin: float = 0.10
    cost_ref_usd: float = 1.0
    rework_ref: float = 1.0
    queue_ref_s: float = 3600.0
    max_lanes: int = 4
    family_cap: int = 3
    hedge_max: int = 1
    default_min_trust: int = 0
    explore_share: float = 0.10
    explore_min_n: int = 5
    bug_window_s: float = 7 * 24 * 3600.0

    @classmethod
    def from_config(cls, cfg: Config) -> Weights:
        s = cfg.swarm
        return cls(
            w_critical=s.w_critical,
            w_fit=s.w_fit,
            w_cost=s.w_cost,
            w_rework=s.w_rework,
            w_queue=s.w_queue,
            fit_margin=s.fit_margin,
            cost_ref_usd=s.cost_ref_usd,
            rework_ref=s.rework_ref,
            queue_ref_s=s.queue_ref_s,
            max_lanes=s.max_lanes,
            family_cap=s.family_cap,
            hedge_max=s.hedge_max,
            default_min_trust=s.default_min_trust,
            explore_share=s.explore_share,
            explore_min_n=s.explore_min_n,
            bug_window_s=s.bug_window_s,
        )


def lane_prices(cat: modelreg.Catalog, model: str, route: str | None = None) -> dict[str, Any]:
    """``price_in``/``price_out`` (USD per million tokens) and ``window`` for ``model``;
    a value the catalog cannot vouch for is None."""
    out: dict[str, Any] = {}
    for key in ("price_in", "price_out", "window"):
        out[key] = modelreg.read(cat, model, key, route).value
    return out

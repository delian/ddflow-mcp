"""Limit and budget headroom for the swarm scheduler (SW-headroom). Pure.

The data is the layered ``[prices]`` table of TE-limit-aware-routing (``limit_routing``);
this module adds no second price table. A lane's model (and, when it has one, its
subscription id) may carry a spending limit. Two uses:

* a hard filter (``refusals``): remaining headroom at or under the floor, or under the
  estimated cost of the item, refuses the lane. No score weight can override it.
* a score term (``term``): the share of the limit still unspent, in [0, 1], times
  ``[swarm] w_headroom``.

A limit that is not on record is UNKNOWN here: the term is neutral and nothing is refused.
(``limit_routing.pick`` is stricter because it chooses a model on faith; a scheduler that
refused every lane without a table would stop a swarm that never set one.)
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ...config_sections import prices as PR
from .. import limit_routing as LR

NEUTRAL = 0.5  # no bonus, no penalty for a figure nothing has recorded


@dataclass(frozen=True)
class Standing:
    """What is left of the tightest known limit binding a lane. ``known`` False: no limit
    on record for its model or subscription."""

    remaining: float = 0.0
    limit: float = 0.0
    known: bool = False


def standing(
    prices: PR.PricesConfig,
    model: str,
    spent: Mapping[str, float] | None = None,
    *,
    subscription: str = "",
) -> Standing:
    """The tightest remaining headroom over the lane's model and subscription keys,
    computed by ``limit_routing.headroom`` from the layered table."""
    best: Standing | None = None
    for key in (model, subscription):
        if not key or prices.limit_of(key) is None:
            continue
        h = LR.headroom(prices, key, dict(spent or {}))
        cur = Standing(h.remaining, float(prices.limit_of(key) or 0.0), True)
        if best is None or cur.remaining < best.remaining:
            best = cur
    return best or Standing()


def effective_floor(configured: float, requested: float = 0.0) -> float:
    """The floor in force: a caller may tighten the configured floor, never loosen it."""
    return max(float(configured), float(requested))


def refusals(s: Standing, *, floor_usd: float = 0.0, need_usd: float | None = None) -> list[str]:
    """Why the lane is out of headroom; empty for an unknown limit (neutral)."""
    if not s.known:
        return []
    floor = max(0.0, float(floor_usd))
    if s.remaining <= floor:
        return [
            f"headroom exhausted: {s.remaining:.4g} USD left of {s.limit:.4g}, floor {floor:.4g}"
        ]
    if need_usd is not None and need_usd > s.remaining:
        return [f"headroom {s.remaining:.4g} USD below the estimated cost {need_usd:.4g}"]
    return []


def term(s: Standing) -> float:
    """Share of the limit still unspent in [0, 1]; neutral when unknown."""
    if not s.known:
        return NEUTRAL
    if s.limit <= 0:
        return 0.0
    return min(1.0, max(0.0, s.remaining / s.limit))

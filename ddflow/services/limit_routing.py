"""Route work by remaining headroom per model (TE-limit-aware-routing).

The `[prices]` section holds the layered spending limits and reset cadence
(`config_sections.prices`); the model catalog holds per-model prices. For a mechanical item
the launcher routes to the cheapest enabled model with remaining headroom; reviewers and
design stay on the strongest available model satisfying the cross-family rule, which is the
caller's concern, not this module's. A limit that is not on record is UNKNOWN: it is
skipped, never treated as headroom.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config_sections import prices as PR


@dataclass(frozen=True)
class Headroom:
    """What remains to spend on one model for the routing decision, or why not."""

    model: str
    remaining: float
    #: False when the model may not be used (exhausted, or no limit on record). A model with
    #: NO limit is UNKNOWN, never headroom, so ``known`` is False for it the same as an
    #: exhausted one.
    known: bool


def headroom(
    prices: PR.PricesConfig, model: str, spent: dict[str, float] | None = None
) -> Headroom:
    """Remaining USD headroom for ``model`` over its reset window, or why not.

    ``spent`` maps model id -> USD already spent in the current window. A model with no
    limit on record -- the limit itself, NOT the price -- is not usable there."""
    limit = prices.limit_of(model)
    if limit is None:
        return Headroom(model, 0.0, False)
    remaining = limit - float((spent or {}).get(model, 0.0))
    return Headroom(model, remaining, remaining > 0.0)


def pick(
    prices: PR.PricesConfig,
    by_price: list[tuple[str, float]],
    spent: dict[str, float] | None = None,
) -> str | None:
    """The model (id) to route work to, or None when none may be used.

    ``by_price`` is ``(model_id, price_per_mtok)`` for every model in the caller's
    preference order: the catalog's cheapest first for mechanical work, the strongest for
    a reviewer. An exhausted model is skipped, and one whose limit is UNKNOWN is skipped
    like an exhausted one -- unknown limit data is not headroom."""
    for model, _unused in by_price:
        if headroom(prices, model, spent).known:
            return model
    return None

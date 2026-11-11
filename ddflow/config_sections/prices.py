"""The `[prices]` section: the layered spending-limit and reset table (TE-limit-aware-routing).

The model catalog (`services.modelreg`, DRV-model-catalog) already records each model's
per-token price; this section holds what the catalog cannot: how much is LEFT to spend per
model per subscription window. Layered the same way ddflow's config already layers
(company floor -> project -> user): `ddflow/config` layering over the whole `[prices]`
table per layer, so a project (or user) replaces the table it sets (this section carries
no per-model inheritance below its own layer). `services.limit_routing` reads it; routing
never treats a limit it cannot read as headroom.

Re-exported from `ddflow.config`, which assembles `Config` from every section.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ._docs import declare, knob


@declare("prices")
@dataclass
class PricesConfig:
    """Layered spending limits: model id -> limit and reset, company floor first."""

    #: USD a model may spend per reset window, by model id (or by subscription id). A model
    #: with no value here, and no inherited value, has no limit on record: routing treats
    #: that UNKNOWN, never as headroom.
    limit: dict[str, Any] = knob(doc="USD a model may spend per reset window, by model id (or subscription id). A model with no value here (and no inherited value) has no limit on record: routing treats that UNKNOWN, never headroom.", factory=dict)
    #: The reset cadence per model id, e.g. "1d", "1mo"; empty value means the section's
    #: default "1mo".
    reset: dict[str, Any] = knob(doc='The reset cadence per model id, e.g. "1d", "1mo"; empty value means the section default "1mo".', factory=dict)

    def limit_of(self, model: str) -> float | None:
        """The layered limit for ``model``, or None when nothing is on record for it.

        A value that is not a number (a string such as "unlimited") degrades to None --
        it is UNKNOWN, never a number routing would subtract from."""
        raw = self.limit.get(model)
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError, OverflowError):
            return None

    def reset_of(self, model: str) -> str:
        """The cadence that resets ``model``'s spend, or the section default "1mo"."""
        return str(self.reset.get(model) or "1mo")

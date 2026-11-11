"""The `[prices]` section: the layered spending-limit and reset table (TE-limit-aware-routing).

The model catalog (`services.modelreg`, DRV-model-catalog) already records each model's
per-token price; this section holds what the catalog cannot: how much is LEFT to spend per
model per subscription window. Layered the same way ddflow's config already layers
(company floor -> project -> user): an absent value in a later layer inherits the one
above, so a project (or user) overrides only the models it spends differently.
`services.limit_routing` reads it; routing never treats a limit it cannot read as headroom.

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
        """The layered limit for ``model``, or None when nothing is on record for it."""
        raw = self.limit.get(model)
        return None if raw is None else float(raw)

    def reset_of(self, model: str) -> str:
        """The cadence that resets ``model``'s spend, or the section default "1mo"."""
        return str(self.reset.get(model) or "1mo")

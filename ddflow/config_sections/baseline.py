"""The `[baseline]` section: the per-sha baseline store (GM-baseline-store).

A new file here is a new section, nothing more registers. The store
(:mod:`ddflow.services.baseline_store`) reads ``keep`` to prune what it remembers. Later
baseline tasks (rerun-alone, known-red registry) add their knobs here too.
"""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, int_at_least, knob


@declare("baseline")
@dataclass
class BaselineConfig:
    """What ddflow remembers of how the base branch's tests fail."""

    keep: int = knob(
        14,
        doc="Days the baseline store keeps a measured base sha (`.ddflow/local/baseline-store/`, git-ignored, per project). An entry older than this is pruned the next time the store is written; a base sha not in the store is measured once under the suite lock and stored. 0 keeps nothing: every run measures the base again.",
        check=int_at_least(0),
    )

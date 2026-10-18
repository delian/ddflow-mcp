"""The `[evaluation]` section: the shared evaluation harness's knobs (SE-eval-harness).

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ._docs import declare, knob


def _share(v: Any) -> str:
    ok = isinstance(v, int | float) and not isinstance(v, bool) and 0.0 <= v <= 1.0
    return "" if ok else "must be a number between 0 and 1"


@declare("evaluation")
@dataclass
class EvaluationConfig:
    """When a workflow has caught enough of its planted faults to be enabled by default."""

    max_miss_rate: float = knob(
        0.0,
        doc="The share (0-1) of planted faults a workflow may miss in the shared evaluation harness and still be enabled by default. 0 (default): it must catch every one (D-shared-engines-r2: a workflow is enabled by default only after it catches its planted set).",
        check=_share,
    )
    min_fixtures: int = knob(
        1,
        doc="Fixtures with at least one planted fault that a workflow needs before its miss rate counts as evidence. A clean fixture (nothing planted) measures false positives and does not count.",
        check=lambda v: (
            ""
            if isinstance(v, int) and not isinstance(v, bool) and v >= 1
            else "must be an integer >= 1"
        ),
    )

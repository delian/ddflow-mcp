"""Small statistics for the metrics views: percentiles and the "enough samples" rule.

A figure that cannot be computed is None, never zero (a median of nothing is not 0 s). A
figure from fewer than ``[metrics] min_samples`` values is None too, with its ``n`` shown.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def percentile(values: Sequence[float], pct: float) -> float | None:
    """The ``pct``th percentile (linear interpolation) of ``values``; None when empty."""
    if not values:
        return None
    xs = sorted(values)
    pos = (len(xs) - 1) * min(max(pct, 0.0), 100.0) / 100.0
    lo, hi = math.floor(pos), math.ceil(pos)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def stat(values: Sequence[float], min_n: int, *, unit: str = "s") -> dict:
    """``{n, p50_<unit>, p90_<unit>, max_<unit>}``; every figure None under ``min_n`` values
    (the count stays: the reader sees how thin it is)."""
    n = len(values)
    if n < max(min_n, 1):
        return {"n": n, f"p50_{unit}": None, f"p90_{unit}": None, f"max_{unit}": None}
    return {
        "n": n,
        f"p50_{unit}": percentile(values, 50),
        f"p90_{unit}": percentile(values, 90),
        f"max_{unit}": max(values),
    }


def rate(part: int, whole: int, min_n: int) -> float | None:
    """``part / whole``, None when ``whole`` is under ``min_n`` (or zero)."""
    return part / whole if whole >= max(min_n, 1) else None

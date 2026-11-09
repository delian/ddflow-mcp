"""Regression and drift detection over a perf series (PM-detect).

Pure functions, no I/O. A step is a change that BOTH a relative threshold (default +20%)
and an absolute floor (default +5 s) must exceed, on at least `detect_min_samples` samples,
persisting across `detect_persistence_windows` consecutive windows -- against a baseline per
env-fingerprint class, so a changed commit is separated from a changed machine. An EWMA band
catches slow creep that a fixed threshold never sees (1% a week). Returns a Finding (series,
window, effect size, sample size, suspected change) or None; a probe that cannot run is None,
never zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from ddflow.config_sections.perf_metrics import PerfMetricsConfig


def mad(values: list[float]) -> float:
    """Median absolute deviation from the median (robust scale)."""
    m = median(values)
    return median([abs(v - m) for v in values])


def _robust_sigma(values: list[float]) -> float:
    """1.4826 * MAD as a robust sigma (matches the normal distribution's SD)."""
    return 1.4826 * mad(values)


def median_shift(baseline: list[float], window: list[float]) -> tuple[float, float, float]:
    """(median change, relative change, robust z-score) of ``window`` against ``baseline``."""
    bmed = median(baseline)
    wmed = median(window)
    sigma = _robust_sigma(baseline) or 1.0
    return wmed - bmed, (wmed - bmed) / bmed if bmed else 0.0, (wmed - bmed) / sigma


def _ewma(values: list[float], alpha: float) -> float:
    """Exponentially-weighted mean (last value = the drift seat)."""
    out = values[0]
    for v in values[1:]:
        out = alpha * v + (1 - alpha) * out
    return out


def _creep_exceeded(baseline: list[float], window: list[float], alpha: float, rel: float) -> bool:
    """Whether the EWMA of the window drifted past the relative threshold over the baseline."""
    b = median(baseline)
    return _ewma(window, alpha) > b * (1 + rel) if b else _ewma(window, alpha) > 0


@dataclass(frozen=True)
class Finding:
    """One detected regression: what series and window, how big, on what machine class."""

    series: str
    env: str
    start: float
    end: float
    median_s: float  # the window's median
    effect_size: float  # relative change vs baseline median
    absolute_s: float  # absolute change vs baseline median
    sample_size: int
    suspected: str


def detect_series(
    series: str,
    env: str,
    baseline: list[float],
    window: list[float],
    start: float = 0.0,
    end: float = 0.0,
    *,
    cfg: PerfMetricsConfig | None = None,
    suspected: str = "",
) -> Finding | None:
    """One step or creep over ``window`` against ``baseline``. Returns None below the sample
    floor or under both thresholds -- the two are distinct, and a "none" is not a pass."""
    cfg = cfg or PerfMetricsConfig()
    if len(baseline) < max(cfg.detect_min_samples, 2) or len(window) < max(cfg.detect_min_samples, 2):
        return None
    abs_s, rel, _z = median_shift(baseline, window)
    stepped = rel >= cfg.detect_relative_threshold and abs_s >= cfg.detect_absolute_floor_s
    crept = _creep_exceeded(baseline, window, cfg.detect_creep_alpha, cfg.detect_relative_threshold)
    if not (stepped or crept):
        return None
    return Finding(series, env, start, end, median(window), rel, abs_s, len(window), suspected)


def detect(
    series: str,
    env: str,
    baseline: list[float],
    windows: list[tuple[float, float, list[float]]],
    *,
    cfg: PerfMetricsConfig | None = None,
    suspected: str = "",
) -> Finding | None:
    """The step that PERSISTS: ``detect_persistence_windows`` consecutive windows must each
    exceed both thresholds before the change becomes a Finding (a one-window spike is not a
    regression)."""
    cfg = cfg or PerfMetricsConfig()
    streak = 0
    for start, end, vals in windows:
        hit = detect_series(series, env, baseline, vals, start, end, cfg=cfg) is not None
        streak = streak + 1 if hit else 0
        if streak >= cfg.detect_persistence_windows:
            return detect_series(series, env, baseline, vals, start, end, cfg=cfg, suspected=suspected)
    return None


def burn_rate(
    short: list[float],
    long: list[float],
    budget_s: float,
    *,
    min_samples: int = 8,
) -> dict[str, float | int | None]:
    """How fast a series consumes a stated budget over a short vs a long window: the p90 of
    each, as the rate-per-run and the budget left (D-perf-metrics-answers shows comparisons)."""
    if len(short) < max(min_samples, 2) or len(long) < max(min_samples, 2):
        return {"short_p50": None, "long_p50": None, "budget_left_short": None}
    sp50 = median(short)
    lp50 = median(long)
    return {
        "short_p50": sp50,
        "long_p50": lp50,
        "budget_left_short": budget_s - sp50,
        "short_n": len(short),
        "long_n": len(long),
    }


__all__ = ["Finding", "burn_rate", "detect", "detect_series", "mad", "median_shift"]

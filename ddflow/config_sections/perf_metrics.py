"""The `[perf_metrics]` section: the perf store's limits (D-perf-metrics-answers).

A new file here is a new section, nothing more registers. The store
(:mod:`ddflow.services.perf.store`) reads these for its ring's size and age cap and its
aggregate sample floor; the sampler, detectors and report read the same knobs. Every
threshold and interval is set four ways (config file, `ddflow config`, MCP, org lock).
"""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, int_at_least, knob


@declare("perf_metrics")
@dataclass
class PerfMetricsConfig:
    """The perf store's limits: what the local ring keeps and when a percentile is honest."""

    ring_max_bytes: int = knob(
        2_000_000,
        doc="Most bytes one raw-sample ring (`.ddflow/local/perf/<series>.jsonl`) keeps; when a series exceeds it the oldest samples are dropped first (flowstate precedent). Raw detail only -- rollups and aggregates are committed.",
        check=int_at_least(1),
    )
    ring_max_age_s: int = knob(
        7 * 86400,
        doc="Seconds a raw sample stays in the local ring before the store trims it.",
        check=int_at_least(1),
    )
    aggregate_min_samples: int = knob(
        3,
        doc="Fewest samples a daily aggregate needs before its p50/p90/max are reported; below it the count is reported and the percentiles are None, never zero.",
        check=int_at_least(1),
    )
    detect_min_samples: int = knob(
        8,
        doc="Fewest samples a series needs before the regression detector will call a change; a smaller window says not-enough-data, never an alert.",
        check=int_at_least(2),
    )
    detect_relative_threshold: float = knob(
        0.20,
        doc="The relative step a drift detector must exceed to fire -- the change is at least this fraction above the baseline median (D-perf-metrics-answers default +20%).",
        check=lambda v: (
            ""
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0
            else "must be a number >= 0"
        ),
    )
    detect_absolute_floor_s: float = knob(
        5.0,
        doc="The absolute step (seconds) a drift detector must also exceed; a +20% change smaller than this is noise, so microseconds do not alert.",
        check=lambda v: (
            ""
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0
            else "must be a number >= 0"
        ),
    )
    detect_persistence_windows: int = knob(
        2,
        doc="How many consecutive windows must exceed both thresholds before a change becomes a finding (a one-window spike is not a regression).",
        check=int_at_least(1),
    )
    detect_creep_alpha: float = knob(
        0.10,
        doc="The EWMA alpha for slow creep that a fixed threshold never sees (1% a week is invisible step-wise); the detector flags an EWMA that drifted past the relative threshold across the window.",
        check=lambda v: (
            ""
            if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v <= 1
            else "must be a number in (0, 1]"
        ),
    )
    detect_creep_threshold: float = knob(
        0.10,
        doc="The LOWER relative bar a slow-creep EWMA must cross (a fixed +20% step bar never catches 1% a week; the creep bar does, accumulated over the window).",
        check=lambda v: (
            ""
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0
            else "must be a number >= 0"
        ),
    )

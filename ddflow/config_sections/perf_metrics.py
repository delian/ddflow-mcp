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

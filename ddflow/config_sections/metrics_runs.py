"""The `[metrics_runs]` section: runs-to-merge and first-pass knobs (D-runs-per-merge-r2).

A new file here is a new section, nothing more registers. The runs-to-merge view
(:mod:`ddflow.services.metrics.runs_to_merge`) reads these: ``runs_window_days`` is the
window the report measures merged items over, and ``outlier_runs`` the outlier flag that
marks an item apart instead of folding it into its phase's average. Every knob is set four
ways (config file, `ddflow config`, MCP, org lock), like each other section.
"""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, int_at_least, knob


@declare("metrics_runs")
@dataclass
class MetricsRunsConfig:
    """Runs-to-merge view knobs (D-runs-per-merge-r2)."""

    runs_window_days: int = knob(
        30,
        doc="The time window `ddflow metrics runs` measures merged items over, in days back from now: an item completed before it does not count. A duration the operator narrows to see the recent trend, or widens to smooth a thin log.",
        check=int_at_least(1),
    )
    outlier_runs: int = knob(
        0,
        doc="The outlier flag: a merged item whose run count reaches this many is labelled an outlier in the report instead of silently widening the phase's average. 0 turns the flag off (r-safe-by-default). The label changes no count.",
        check=lambda v: (
            ""
            if isinstance(v, int) and not isinstance(v, bool) and v >= 0
            else "must be an integer >= 0"
        ),
    )

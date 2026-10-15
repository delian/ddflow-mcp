"""The `[metrics]` section: the log-derived metrics views (`ddflow metrics`).

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, knob


def _percent(value: object) -> str:
    return (
        "" if isinstance(value, int) and value in range(1, 100) else "a percentile between 1 and 99"
    )


@declare("metrics")
@dataclass
class MetricsConfig:
    """Metrics derived from the event log: cycle time, review rounds, waits, forecast, retro."""

    min_samples: int = knob(
        10,
        doc="Fewest samples a metrics figure may rest on. Under it the figure is reported unavailable (n/a, with its sample count), never as a number, and a view with no figure at all exits 2 ('not enough data'). Applies to every figure, the forecast and the figures other services register.",
    )
    top: int = knob(
        5,
        doc="How many rows `ddflow metrics skips` lists for the reasons given per gate and for the agents who skip.",
    )
    forecast_runs: int = knob(
        1000,
        doc="Bootstrap draws behind `ddflow metrics forecast`: each draws one observed duration per link of the critical path and sums them. More draws smooth the range; they do not make it more certain.",
    )
    forecast_seed: int = knob(
        0,
        doc="Seed of the forecast's random draws, so the same log always gives the same range. Change it to see how much the range moves with the draw.",
    )
    forecast_low_pct: int = knob(
        50,
        doc="The percentile of the forecast's simulated critical-path durations reported as the likely end of the range (P50 by default).",
        check=_percent,
    )
    forecast_high_pct: int = knob(
        90,
        doc="The percentile of the forecast's simulated critical-path durations reported as the cautious end of the range (P90 by default).",
        check=_percent,
    )
    grid_step_s: int = knob(
        3600,
        doc="Seconds between the instants `ddflow metrics lanes` replays the plan at (the hourly grid, shared with headroom views).",
    )
    grid_max_folds: int = knob(
        24,
        doc="Most instants the grid sampler folds the log at, newest first. Each fold replays the whole log up to that instant, so this caps the cost of `ddflow metrics lanes`; the answer says when the cap cut the window short.",
    )

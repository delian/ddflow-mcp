"""The `[metrics]` section: the log-derived metrics views (`ddflow metrics`).

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, int_at_least, knob


def _percent(value: object) -> str:
    return (
        ""
        if isinstance(value, int) and not isinstance(value, bool) and value in range(1, 100)
        else "a percentile between 1 and 99"
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
    runs_window_days: int = knob(
        30,
        doc="The time window the runs-to-merge view measures merged items over, in days back from now: an item completed before it does not count. A duration the operator narrows to see the recent trend, or widens to smooth a thin log.",
        check=int_at_least(1),
    )
    outlier_runs: int = knob(
        0,
        doc="The outlier flag: a merged item whose run count reaches this many is labelled an outlier in the runs-to-merge report instead of silently widening the phase's average. 0 turns the flag off (r-safe-by-default). The label changes no count.",
        check=lambda v: (
            ""
            if isinstance(v, int) and not isinstance(v, bool) and v >= 0
            else "must be an integer >= 0"
        ),
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
    stage_size_small: int = knob(
        50,
        doc="Largest change, in lines (insertions plus deletions the gate evidence recorded), that `ddflow metrics stages --group-by size` calls small. An item whose evidence never showed a changed line is grouped as unknown size, never as small.",
    )
    stage_size_large: int = knob(
        300,
        doc="Changes above this many lines are large in `ddflow metrics stages --group-by size`; between the small and the large bound they are medium.",
    )
    lock_probe_max: int = knob(
        50,
        doc="Most recent merges `ddflow metrics locks` replays through `git merge-tree --write-tree` (branch tip onto the target as it was before the merge) to count textual conflicts. Each probe is a few milliseconds; the cap bounds the cost on a long log. 0 probes none and the conflict rate is reported unavailable.",
    )
    lock_red_main_window_s: int = knob(
        86400,
        doc="A merge counts as red-main in `ddflow metrics locks` when a bug is filed against its item within this many seconds after the merge landed.",
    )
    lock_falsifier_conflict_pct: int = knob(
        5,
        doc="The recorded falsifier of the observed-file locking (R-lock-landscape-verified): when textual merge conflicts exceed this percent of merges, `ddflow metrics locks` says the new locking has tripped and names the knob in `lock_revert_knob`.",
    )
    lock_falsifier_red_main_pct: int = knob(
        10,
        doc="The second recorded falsifier: red-main bugs above this percent of merges trip it, with the same advice.",
    )
    lock_revert_key: str = knob(
        "locks.mode",
        doc="The setting that switches the lock lifecycle back to the strict mode, named in `ddflow metrics locks` when a falsifier trips, with the exact `ddflow config --set` and `ddflow_configure` call (the [locks] section is added by the lock-lifecycle knob register). Change it if your organisation locks the mode under another name.",
    )
    lock_revert_value: str = knob(
        "strict",
        doc="The value of `lock_revert_key` that is the strict mode.",
    )

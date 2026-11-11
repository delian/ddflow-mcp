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
    flaky_window_s: int = knob(
        7 * 86400,
        doc="The rolling window the flaky-rate view (services/perf/flakyrate.py) measures flake counts and quarantine proposals over; a flake older than this does not count.",
        check=int_at_least(1),
    )
    flaky_quarantine_after: int = knob(
        3,
        doc="Flake count of one test within flaky_window_s at which the rate view proposes a quarantine (B-sched-flaky owns the lane; PM-flaky only measures and proposes).",
        check=int_at_least(2),
    )
    flaky_quarantine_expiry_s: int = knob(
        30 * 86400,
        doc="Seconds a quarantine proposal stays open (its expiry); after this the test is re-evaluated rather than quarantined forever.",
        check=int_at_least(1),
    )
    flaky_retry_cost_s: float = knob(
        60.0,
        doc="Estimated wall seconds one failed->rerun cycle costs; the rate view multiplies flakes in the window by this for retry cost (the flake log stores no per-retry duration).",
        check=lambda v: (
            ""
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0
            else "must be a number >= 0"
        ),
    )
    budget_gate_p90_s: float = knob(
        180.0,
        doc="A gate wall-time budget: the p90 seconds one gate may take in a window before it is over budget (PM-budgets). The floor only tightens; raising it is a recorded decision.",
        check=lambda v: (
            ""
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0
            else "must be a number > 0"
        ),
    )
    budget_test_file_s: float = knob(
        120.0,
        doc="A per-test-file budget: the seconds one test file's serial run may take before the file is a split candidate (PM-budgets). The floor only tightens; raising it is a recorded decision.",
        check=lambda v: (
            ""
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0
            else "must be a number > 0"
        ),
    )
    budget_stage_lease_wait_s: float = knob(
        120.0,
        doc="A lease-wait budget: the seconds an item may wait for a refused claim's lease before the wait is a finding (PM-budgets). Only goes down; raising it is a recorded decision.",
        check=lambda v: (
            ""
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0
            else "must be a number > 0"
        ),
    )
    budget_stage_merge_wait_s: float = knob(
        120.0,
        doc="A merge-wait budget: the seconds a landed branch may wait to be picked up before the wait is a finding (PM-budgets). Only goes down; raising it is a recorded decision.",
        check=lambda v: (
            ""
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0
            else "must be a number > 0"
        ),
    )
    budget_breach_min_samples: int = knob(
        3,
        doc="Fewest samples a series needs before its p90 is compared to a budget; below it the check is not-enough-data, never a false clear (PM-budgets).",
        check=int_at_least(1),
    )
    budget_severity: str = knob(
        "warn",
        doc="What a budget breach does: 'warn' files a finding only; 'block' also refuses and names the next call (split the file, see the slowest tests, raise with a recorded reason). Ships warn (r-safe-by-default).",
        choices=("warn", "block"),
        strictest=("warn", "a budget stays a finding, never a surprise refusal"),
    )
    prop_min_observations: int = knob(
        3,
        doc="Fewest observed cap values a parallel-cap proposal needs before it will advise a cap (PM-prop-flow); below it the proposal is not-enough-data, never a zero.",
        check=int_at_least(3),
    )
    prop_marginal_gain: float = knob(
        0.25,
        doc="The fraction of the wall clock one more agent must save before its extra parallelism is paid back (PM-prop-flow); below it the cap before it is the proposed parallel cap.",
        check=lambda v: (
            ""
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0
            else "must be a number > 0"
        ),
    )
    prop_batch_dominance: float = knob(
        2.0,
        doc="The suite-lock wait as a multiple of the gate run that triggers a batch-gates proposal (PM-prop-flow).",
        check=lambda v: (
            ""
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0
            else "must be a number > 0"
        ),
    )
    prop_retire_payback: float = knob(
        2.0,
        doc="The multiple of what a stage buys back that its wait must reach before a retire-stage proposal fires (PM-prop-flow).",
        check=lambda v: (
            ""
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0
            else "must be a number > 0"
        ),
    )
    report_default_window: str = knob(
        "28d",
        doc="The time window a perf report measures when none is given: a duration back (7d, 12h) or a date YYYY-MM-DD (parse_since).",
    )
    report_max_rows: int = knob(
        20,
        doc="Most rows the perf report's text/markdown renderer shows per section; the cut line names the omitted count and says the json holds every row (PM-report-surfaces).",
        check=int_at_least(1),
    )
    report_width: int = knob(
        76,
        doc="The width in characters the perf report's text/markdown renderer keeps every line within (terminal and chat alike).",
        check=int_at_least(1),
    )

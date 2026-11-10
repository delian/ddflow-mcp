"""The `[slo]` section: service-level objective targets for ddflow itself (R-observability 6).

The SLIs these targets bound are read from the existing metrics fold
(:mod:`ddflow.services.metrics.slo`), never from a new store. A target is a knob, so it is
set four ways like every other (config file, `ddflow config`, MCP, org lock). Only queue age
and CI false-red get a burn-rate view (their window is long and their signal monotone); every
other SLI rides the existing N-of-W flow-control pattern (PM-detect), so it has no burn-rate
here by design (low traffic).
"""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, int_at_least, knob


def _positive(value: object) -> str:
    return (
        ""
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
        else "must be a number > 0"
    )


def _fraction(value: object) -> str:
    return (
        ""
        if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value < 1
        else "must be a number in (0, 1)"
    )


@declare("slo")
@dataclass
class SloConfig:
    """Service-level objective targets: the number each SLI is held against."""

    target_command_p95_s: float = knob(
        2.0,
        doc="The command-latency target: the p95 wall seconds of a brief or claim command on the host. Bound from the perf store's per-command samples; below the sample floor it is not-collected, never zero (PM-sampler supplies the samples).",
        check=_positive,
    )
    target_gate_p90_s: float = knob(
        300.0,
        doc="The gate-latency target: the p90 wall seconds one gate run may take. A gate whose p90 exceeds this in the window is over target.",
        check=_positive,
    )
    target_queue_age_s: float = knob(
        600.0,
        doc="The queue-age target: the p90 seconds an item waits ready-to-claimed. This is the SLI that gets a burn-rate view (its window is long and its signal monotone).",
        check=_positive,
    )
    target_unavailable_rate: float = knob(
        0.25,
        doc="The unavailable target: the highest share of a gate's RAN verdicts that may be `unavailable` (a reviewer that could not run is never `passed`). A skip is not a pass and is excluded from the rate. One quarter is the contract, so `ci` at ~11 percent is comfortably inside it.",
        check=_fraction,
    )
    target_stuck_lanes: int = knob(
        2,
        doc="The stuck-lane target: the most live lanes that may be idle past `[lanes]` hold without activity before the SLI is over target. A lane that never released and never renewed is waste, not progress.",
        check=int_at_least(0),
    )
    target_ci_false_red_rate: float = knob(
        0.10,
        doc="The CI false-red target: the highest share of merges that produce a red-main finding (a bug filed against the item within `[metrics] lock_red_main_window_s` of the merge) that is not a real regression. It gets a burn-rate view with queue age: a red CI is a capacity burn.",
        check=_fraction,
    )
    target_log_events_per_item: float = knob(
        30.0,
        doc="The log-growth target: the most committed log events one completed item may add. Growth that tracks item count is healthy; growth that tracks retries is rework.",
        check=_positive,
    )
    target_review_finding_rate: float = knob(
        0.60,
        doc="The review-finding signal: the share of critic and rubber_duck rounds that raise a finding. It is a review-quality SIGNAL, not an outage target -- the SLO report shows it but never turns it into a breach (a gate that fails 55-57 percent is doing its job, not down).",
        check=_fraction,
    )
    burn_rate_window_s: int = knob(
        30 * 86400,
        doc="The burn-rate window in seconds over which queue age and CI false-red are extrapolated to a time-to-exhaustion (a 30-day budget viewed per hour).",
        check=int_at_least(1),
    )
    burn_rate_exhaust_s: float = knob(
        3600.0,
        doc="The time-to-exhaustion (seconds) a burn-rate SLI must beat before it is flagged `burning`: the budget is on track to be spent within this horizon. The report only flags; it never refuses (r-safe-by-default).",
        check=_positive,
    )

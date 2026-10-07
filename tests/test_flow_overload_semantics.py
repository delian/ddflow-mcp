"""Adaptive flow: a review that found issues is not overload, and the "limited by" reason
never names a signal that is null now (B767745dee6).

Observed 2026-10-06: `status` said "limited by reviewer latency ratio" for hours while
that ratio was null in almost every sample, and growth was really held by
gate_failure_ratio at 1.2-1.6 -- in a refactoring phase, where failed review gates mean
the reviewer found issues, not that capacity is short.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_flowsignals import MIN, Log, _signals

from ddflow.core import flowcontrol as FC

HEALTHY_HOST = {"load_per_core": 0.06, "memory_pressure": 0.3, "disk_pressure": 0.5}


def test_failed_review_gates_are_not_counted_as_gate_failures():
    log = Log()
    log.outcomes("unit_tests", 2 * 86400.0, passed=40, failed=4)  # the usual week
    log.outcomes("unit_tests", 10 * MIN, passed=10, failed=1)  # the last hour, as usual
    log.outcomes("rubber_duck", 10 * MIN, passed=2, failed=8)  # reviewers found issues
    log.outcomes("critic", 10 * MIN, passed=2, failed=8)
    s = _signals(log)
    assert s.gate_failure_rate is not None and s.gate_failure_rate < 0.2, s
    assert s.gate_failure_ratio is not None and s.gate_failure_ratio <= 1.0, s


def _after_a_decrease(rlr: float | None, gfr: float, in_flight: int = 4) -> FC.Decision:
    """A decrease on reviewer_latency_ratio, then two hours of it reading ``rlr`` while
    gate_failure_ratio reads ``gfr``, with ``in_flight`` agents running throughout."""
    samples = [
        FC.Sample(at=i * 60.0, signals={**HEALTHY_HOST, "reviewer_latency_ratio": 2.5})
        for i in range(4)
    ]
    samples += [
        FC.Sample(
            at=(4 + i) * 60.0,
            signals={**HEALTHY_HOST, "reviewer_latency_ratio": rlr, "gate_failure_ratio": gfr},
        )
        for i in range(120)
    ]
    d = FC.fold_limit(samples, FC.Params(), [(0.0, in_flight)], samples[-1].at)
    assert d.limit < FC.Params().start, "fixture: the early samples should lower the limit"
    return d


def test_limited_by_never_names_a_signal_that_is_null_now():
    """gate_failure_ratio over its low mark (1.5) holds growth: it is what is named."""
    assert _after_a_decrease(None, 1.6).limited_by == "gate_failure_ratio"


def test_with_every_signal_healthy_the_growth_or_demand_is_named():
    """The incident's own values: the ratio at 1.2-1.4, under its low mark, and the
    latency ratio null -- nothing holds the limit but demand (one agent running)."""
    assert _after_a_decrease(None, 1.3, in_flight=1).limited_by == "demand (1 in flight)"


def test_a_decrease_signal_that_recovered_is_not_named():
    """Not null but healthy again (under its low mark): it holds nothing either."""
    assert _after_a_decrease(1.0, 1.6).limited_by == "gate_failure_ratio"

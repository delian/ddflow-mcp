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


def test_limited_by_never_names_a_signal_that_is_null_now():
    """A decrease on reviewer_latency_ratio, then hours of that ratio being null while
    gate_failure_ratio sits in its neutral band: the reason names what holds it now."""
    samples = [
        FC.Sample(at=i * 60.0, signals={**HEALTHY_HOST, "reviewer_latency_ratio": 2.5})
        for i in range(4)
    ]
    samples += [
        FC.Sample(
            at=(4 + i) * 60.0,
            signals={**HEALTHY_HOST, "reviewer_latency_ratio": None, "gate_failure_ratio": 1.6},
        )
        for i in range(120)
    ]
    now = samples[-1].at
    d = FC.fold_limit(samples, FC.Params(), [(0.0, 4)], now)
    assert d.limit < FC.Params().start, "fixture: the early samples should lower the limit"
    assert d.limited_by != "reviewer_latency_ratio", d
    assert d.limited_by == "gate_failure_ratio", d

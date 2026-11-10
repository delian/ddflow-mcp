"""Proposal generators for the perf workflow (PM-prop-flow): propose, never override."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from ddflow.config_sections.perf_metrics import PerfMetricsConfig
from ddflow.services.perf import prop_flow


def _observed():
    # wall shrinks 100 -> 60 -> 45 -> 44: the last step saves only 1 s, so cap 3 is the advice.
    return [
        prop_flow.CapObserved(1, 100.0, 100.0, 9000, 0.3, 2.0),
        prop_flow.CapObserved(2, 60.0, 105.0, 9500, 0.4, 4.0),
        prop_flow.CapObserved(3, 45.0, 110.0, 9900, 0.5, 7.0),
        prop_flow.CapObserved(4, 44.0, 112.0, 10100, 0.5, 12.0),
    ]


def test_below_the_observation_floor_is_not_a_proposal_or_a_zero():
    cfg = PerfMetricsConfig(prop_min_observations=3)
    assert prop_flow.propose_parallel_cap(_observed()[:2], cfg=cfg) is None


def test_a_parallel_cap_is_proposed_with_evidence():
    cfg = PerfMetricsConfig(prop_min_observations=3)
    p = prop_flow.propose_parallel_cap(_observed(), cfg=cfg)
    assert p is not None and p.kind == "parallel_cap"
    assert p.advice == 3  # the 4th agent saved only 1 s: not paid back
    assert p.evidence["n"] == 4
    assert "wall_s" in p.evidence  # wall clock, cpu, rss, load and lease wait are the evidence
    assert "rss_kb" in p.evidence and "lease_wait_s" in p.evidence


def test_the_evidence_carries_each_observed_cap():
    cfg = PerfMetricsConfig(prop_min_observations=3)
    p = prop_flow.propose_parallel_cap(_observed(), cfg=cfg)
    by_cap = p.evidence["by_cap"]
    assert [o["cap"] for o in by_cap] == [1, 2, 3, 4]


def test_a_cap_that_keeps_paying_for_itself_is_the_ceiling():
    cfg = PerfMetricsConfig(prop_min_observations=3)
    gains = [
        prop_flow.CapObserved(1, 100.0, 100.0, 9000, 0.3, 2.0),
        prop_flow.CapObserved(2, 60.0, 110.0, 9500, 0.4, 4.0),
        prop_flow.CapObserved(3, 40.0, 120.0, 9900, 0.5, 7.0),
    ]  # every step still saves >= 1/4 of the wall: keep going -> cap 3
    p = prop_flow.propose_parallel_cap(gains, cfg=cfg)
    assert p.advice == 3


def test_narrow_globs_proposes_the_file_with_the_longest_lease_wait():
    waits = {"src/a.py": 5.0, "src/b.py": 40.0, "src/c.py": 12.0}
    p = prop_flow.propose_narrow_globs(waits, PerfMetricsConfig(budget_stage_lease_wait_s=30.0))
    assert p is not None and p.kind == "narrow_globs"
    assert p.advice["glob"] == "src/b.py" and p.advice["lease_wait_s"] == pytest.approx(40.0)


def test_narrow_globs_stays_quiet_with_no_wait_over_budget():
    cfg = PerfMetricsConfig(budget_stage_lease_wait_s=120.0)
    assert prop_flow.propose_narrow_globs({"a.py": 5.0, "b.py": 6.0}, cfg) is None


def test_batch_gates_proposes_when_the_suite_lock_wait_dominates():
    cfg = PerfMetricsConfig(prop_batch_dominance=2.0)
    p = prop_flow.propose_batch_gates(25.0, 120.0, cfg=cfg)
    assert p is not None and p.kind == "batch_gates"
    assert p.advice == pytest.approx(120.0 / 25.0)


def test_batch_gates_stays_quiet_when_the_lock_does_not_dominate():
    cfg = PerfMetricsConfig(prop_batch_dominance=2.0)
    assert prop_flow.propose_batch_gates(20.0, 25.0, cfg=cfg) is None


def test_batch_gates_fires_when_a_zero_gate_run_has_any_lock_wait():
    cfg = PerfMetricsConfig(prop_batch_dominance=2.0)
    p = prop_flow.propose_batch_gates(0.0, 30.0, cfg=cfg)
    assert p is not None  # a zero gate run is the strongest dominance, never not-enough-data


def test_batch_gates_stays_quiet_when_neither_ran():
    # both zero: a probe that did not run, never a dominance proposal
    cfg = PerfMetricsConfig(prop_batch_dominance=2.0)
    assert prop_flow.propose_batch_gates(0.0, 0.0, cfg=cfg) is None


def test_retire_stage_proposes_when_the_wait_is_not_paid_back():
    cfg = PerfMetricsConfig(prop_retire_payback=2.0)
    p = prop_flow.propose_retire_stage("review_wait", 200.0, 50.0, cfg=cfg)
    assert p is not None and p.kind == "retire_stage"
    assert p.advice == "review_wait" and p.because


def test_parallel_cap_collapses_two_rows_of_the_same_cap():
    # two observations of cap 2 are that cap twice, never "cap 2 then one more agent"
    cfg = PerfMetricsConfig(prop_min_observations=3, prop_marginal_gain=0.1)
    rows = [
        prop_flow.CapObserved(2, 100.0),
        prop_flow.CapObserved(2, 10.0),
        prop_flow.CapObserved(3, 9.0),
    ]
    p = prop_flow.propose_parallel_cap(rows, cfg=cfg)
    assert p.advice == 3  # cap 2 -> cap 3 still pays (10 -> 9 within the distinct caps)


def test_retire_stage_stays_quiet_when_the_wait_is_paid_back():
    cfg = PerfMetricsConfig(prop_retire_payback=2.0)
    assert prop_flow.propose_retire_stage("merge_wait", 20.0, 100.0, cfg=cfg) is None


def test_retire_stage_fires_when_the_stage_buys_nothing_back():
    cfg = PerfMetricsConfig(prop_retire_payback=2.0)
    p = prop_flow.propose_retire_stage("lint", 30.0, 0.0, cfg=cfg)
    assert p is not None and p.kind == "retire_stage"  # paid back 0: never a clear

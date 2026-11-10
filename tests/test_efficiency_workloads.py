"""Regression tests for the reference workloads (EFF-workloads).

The invariant these pin: a workload passes only when ddflow's OWN records carry the
named evidence -- an agent that claims success without the records is scored FAIL, never
PASS.
"""

from __future__ import annotations

import ddflow.services.efficiency as EFF
import ddflow.services.efficiency.workloads as W


def _workloads():
    return W.load_workloads()


def test_the_fixed_set_loads_and_names_a_pass_rule_for_every_workload():
    wl = _workloads()
    assert set(wl) == {
        "claim-and-finish-a-task",
        "run-a-gate",
        "record-a-bug-with-regression-test",
        "resolve-a-refusal",
    }
    for wid, workload in wl.items():
        assert workload.id == wid
        assert workload.steps, f"{wid}: a workload must declare its steps"
        assert workload.pass_rule.kind, f"{wid}: a workload must declare a pass rule"


def test_a_scripted_fake_lane_run_is_deterministic_and_passes():
    wl = _workloads()["claim-and-finish-a-task"]
    first = W.run_fake_lane(wl)
    second = W.run_fake_lane(wl)
    # Deterministic: same records on every run, no spend, no network.
    assert first.outcome == W.PASS
    assert first == second


def test_a_claimed_success_without_the_records_scores_failed():
    # The falsifier: the agent SAYS it finished, but the records have no item.completed
    # and no gate outcomes. Words do not score a pass -- only ddflow's records do.
    wl = _workloads()["claim-and-finish-a-task"]
    claimed_but_unrecorded = [
        W.Record("item.started", "demo.task", {"kind": "task"}),
    ]
    report = W.evaluate(wl, claimed_but_unrecorded)
    assert report.outcome == W.FAIL
    assert any("item.completed" in m for m in report.missing)


def test_a_gate_that_was_never_run_is_not_a_pass():
    # Silence is not a pass: "run-a-gate" must record an actual gate.passed outcome.
    wl = _workloads()["run-a-gate"]
    report = W.evaluate(
        wl,
        [W.Record("gate.started", "demo.task", {"gate": "unit_tests"})],
    )
    assert report.outcome == W.FAIL
    assert report.missing == ("gate.passed:unit_tests",)


def test_a_bug_fixed_without_a_named_regression_test_scores_failed():
    wl = _workloads()["record-a-bug-with-regression-test"]
    report = W.evaluate(
        wl,
        [
            W.Record("bug.found", "demo.bug", {"summary": "demo"}),
            W.Record("bug.fixed", "demo.bug", {"lesson": "fixed"}),
        ],
    )
    assert report.outcome == W.FAIL
    assert any("regression-test" in m for m in report.missing)


def test_a_refusal_that_still_stands_scores_failed():
    wl = _workloads()["resolve-a-refusal"]
    report = W.evaluate(
        wl,
        [W.Record("gate.failed", "demo.task", {"gate": "merge", "reason": "refused"})],
    )
    assert report.outcome == W.FAIL
    assert report.missing == ("resolution (gate.passed):merge",)


def test_a_complete_loop_with_its_records_scores_passed():
    wl = _workloads()["claim-and-finish-a-task"]
    records = [
        W.Record("item.started", "demo.task", {"kind": "task"}),
        W.Record("gate.passed", "demo.task", {"gate": "implement"}),
        W.Record("gate.passed", "demo.task", {"gate": "unit_tests"}),
        W.Record("worktree.merged", "demo.task", {"sha": "abc"}),
        W.Record("item.completed", "demo.task", {"kind": "task"}),
    ]
    report = W.evaluate(wl, records)
    assert report.outcome == W.PASS


def test_a_live_run_refuses_without_budget_and_consent():
    wl = _workloads()["claim-and-finish-a-task"]
    try:
        W.run_live(wl, "/tmp/throwaway", budget=None, consent="")
    except W.LiveRunRefused:
        pass
    else:
        raise AssertionError("live run without budget and consent must be refused")


def test_fake_lane_never_refuses_and_scores_pass_for_the_whole_set():
    reports = W.run_all()
    assert set(reports) == set(_workloads())
    assert all(r.outcome == W.PASS for r in reports.values())


def test_exported_symbols_are_the_module_ones():
    assert EFF.load_workloads is W.load_workloads
    assert EFF.evaluate is W.evaluate
    assert EFF.run_fake_lane is W.run_fake_lane

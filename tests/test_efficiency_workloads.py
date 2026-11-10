"""Regression tests for the reference workloads (EFF-workloads).

The invariant these pin: a workload passes only when ddflow's OWN records carry the
named evidence -- an agent that claims success without the records is scored FAIL, never
PASS.
"""

from __future__ import annotations

import json
import os
import tempfile

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


def test_a_truthy_but_nameless_regression_test_does_not_count():
    wl = _workloads()["record-a-bug-with-regression-test"]
    for fix in ({"regression_test": True}, {"regression_tests": [""]}, {"regression_tests": []}):
        report = W.evaluate(
            wl,
            [
                W.Record("bug.found", "demo.bug", {"summary": "demo"}),
                W.Record("bug.fixed", "demo.bug", fix),
            ],
        )
        assert report.outcome == W.FAIL, fix


def test_a_non_object_jsonl_line_is_skipped_not_crashing():
    import ddflow.services.efficiency.workloads as Wmod

    tmp = tempfile.mkdtemp(prefix="eff-log-")
    events_dir = os.path.join(tmp, ".ddflow", "events")
    os.makedirs(events_dir)
    with open(os.path.join(events_dir, "shard.jsonl"), "w", encoding="utf-8") as log:
        log.write("[]\nnull\n")
        log.write(json.dumps({"kind": "item.started", "subject": "demo.task", "data": {}}) + "\n")
    records = Wmod.records_from_log(tmp)
    assert any(r.kind == "item.started" for r in records)


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


def test_a_live_run_without_a_runner_scores_unrun_not_pass_or_fail():
    # A live run that ran no steps must not be scored: UNRUN, never PASS (a stale log
    # read as this run's success) and never FAIL (a run that was never attempted).
    wl = _workloads()["claim-and-finish-a-task"]
    report = W.run_live(wl, "/tmp/throwaway", budget={"max_spend": 1}, consent="recorded")
    assert report.outcome == W.UNRUN


def test_a_live_run_does_not_score_a_throwaway_log_as_pass():
    # The throwaway project's log already holds every pass event, but THIS run did not
    # run its steps (no runner), so it is UNRUN -- the stale log must not read as this
    # run's success.
    wl = _workloads()["claim-and-finish-a-task"]
    tmp = tempfile.mkdtemp(prefix="eff-throwaway-")
    events_dir = os.path.join(tmp, ".ddflow", "events")
    os.makedirs(events_dir)
    stale = [
        {"kind": "item.started", "subject": "demo.task", "data": {"kind": "task"}},
        {"kind": "gate.passed", "subject": "demo.task", "data": {"gate": "implement"}},
        {"kind": "gate.passed", "subject": "demo.task", "data": {"gate": "unit_tests"}},
        {"kind": "item.completed", "subject": "demo.task", "data": {"kind": "task"}},
    ]
    with open(os.path.join(events_dir, "fake.jsonl"), "w", encoding="utf-8") as log:
        for event in stale:
            log.write(json.dumps(event) + "\n")
    assert W.records_from_log(tmp), "the throwaway project must hold the stale records"
    report = W.run_live(wl, tmp, budget={"max_spend": 1}, consent="recorded")
    assert report.outcome == W.UNRUN
    assert report.outcome != W.PASS


def test_a_live_run_scores_only_the_records_the_runner_produced():
    wl = _workloads()["claim-and-finish-a-task"]
    produced = [
        W.Record("item.started", "demo.task", {"kind": "task"}),
        W.Record("gate.passed", "demo.task", {"gate": "implement"}),
        W.Record("gate.passed", "demo.task", {"gate": "unit_tests"}),
        W.Record("item.completed", "demo.task", {"kind": "task"}),
    ]
    report = W.run_live(
        wl,
        "/tmp/throwaway",
        budget={"max_spend": 1},
        consent="recorded",
        runner=lambda w, p: produced,
    )
    assert report.outcome == W.PASS


def test_a_gate_outcome_recorded_on_a_different_item_does_not_count():
    # PASS needs the gate to have run on THIS item; a same-named gate on another item
    # gives the 'other task' its own record, not this one's.
    wl = _workloads()["claim-and-finish-a-task"]
    records = [
        W.Record("item.started", "demo.task", {"kind": "task"}),
        W.Record("gate.passed", "other.task", {"gate": "implement"}),
        W.Record("gate.passed", "other.task", {"gate": "unit_tests"}),
        W.Record("item.completed", "demo.task", {"kind": "task"}),
    ]
    report = W.evaluate(wl, records)
    assert report.outcome == W.FAIL
    assert any("gate outcome" in m for m in report.missing)


def test_fake_lane_never_refuses_and_scores_pass_for_the_whole_set():
    reports = W.run_all()
    assert set(reports) == set(_workloads())
    assert all(r.outcome == W.PASS for r in reports.values())


def test_exported_symbols_are_the_module_ones():
    assert EFF.load_workloads is W.load_workloads
    assert EFF.evaluate is W.evaluate
    assert EFF.run_fake_lane is W.run_fake_lane

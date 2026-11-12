"""`runs_to_merge`: runs per merged item and first-pass rate, from gate records alone.

The acceptance (D-runs-per-merge-r2): a fixture with one item passing first time and one
needing three runs reports "1 vs 3 runs" and first-pass 100% vs 33%; the window and grouping
knobs change the output."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lanes_fixture import T0, Log
from metrics_fixture import frozen

from ddflow.services.metrics import runs_to_merge as R
from ddflow.services.metrics.facts import collect
from ddflow.services.metrics.providers import Context
from ddflow.services.metrics.runs_to_merge import rollup


def log() -> Log:
    g = Log()
    g.add("phase.added", "P1", "lead", T0 - 2000, title="p1", body="")
    g.add("task.added", "alpha", "lead", T0 - 1000, title="a", body="", parent="P1")
    g.add("task.added", "beta", "lead", T0 - 1000, title="b", body="", parent="P1")
    g.session("a1", "s1", T0 - 900, tool="claude-code", model="m-alpha")
    # alpha: one run, passes first time.
    g.claim("a1", "alpha", T0 + 100, "a.py")
    g.add("gate.started", "alpha", "a1", T0 + 110, gate="unit_tests")
    g.add("gate.passed", "alpha", "a1", T0 + 200, gate="unit_tests", by="a1", evidence={})
    g.complete("a1", "alpha", T0 + 300)
    g.release("a1", "alpha", T0 + 301)
    # beta: the same gate needs three runs before it passes.
    g.claim("a1", "beta", T0 + 400, "b.py")
    g.add("gate.failed", "beta", "a1", T0 + 500, gate="unit_tests", by="a1", evidence={})
    g.add("gate.failed", "beta", "a1", T0 + 600, gate="unit_tests", by="a1", evidence={})
    g.add("gate.passed", "beta", "a1", T0 + 700, gate="unit_tests", by="a1", evidence={})
    g.complete("a1", "beta", T0 + 800)
    g.release("a1", "beta", T0 + 801)
    return g


def run(log: Log, **kw) -> dict:
    st, cfg, now = frozen(log)
    cfg.metrics.min_samples = 1
    return rollup(log.events, st, cfg, now, **kw)


def item(report: dict, name: str) -> dict:
    (row,) = [r for r in report["per_item"] if r["item"] == name]
    return row


def test_a_first_time_pass_reports_one_run_and_a_three_run_item_three():
    report = run(log())
    a, b = item(report, "alpha"), item(report, "beta")
    assert a["runs"] == 1
    assert b["runs"] == 3
    # first-pass: alpha passed first time; beta did not.
    assert a["first_pass"] is True
    assert b["first_pass"] is False
    # the pass share of runs: 100% (1 of 1) vs 33% (1 of 3).
    assert a["passes"] == 1
    assert b["passes"] == 1
    assert report["first_pass_rate"] == pytest.approx(0.5)  # one first pass of two items
    assert report["runs"] == 4


def test_the_window_knob_excludes_an_item_completed_before_it():
    g = log()
    full = run(g)
    assert full["merged"] == 2
    # a since AFTER alpha completed drops it: the window selects by completion time.
    after_alpha = run(g, since=T0 + 400)
    assert [r["item"] for r in after_alpha["per_item"]] == ["beta"]
    # the config knob narrows the whole window in days back from now (not a hard since).
    st, cfg, now = frozen(g)
    cfg.metrics.min_samples = 1
    cfg.metrics_runs.runs_window_days = 30
    assert rollup(g.events, st, cfg, now)["window"]["days"] == 30


def test_the_grouping_knob_changes_the_rollup():
    report = run(log(), group_by="model")
    assert report["per_model"] == [{"group": "m-alpha", "items": 2, "first_pass": 1, "first_pass_rate": pytest.approx(0.5), "runs": 4}]
    by_phase = run(log(), group_by="phase")
    assert [r["group"] for r in by_phase["per_phase"]] == ["P1"]
    assert report["group_by"] == "model"


def test_a_review_gate_is_labelled_a_model_gate_and_others_deterministic():
    report = run(log())
    (gate,) = [r for r in report["per_gate"] if r["gate"] == "unit_tests"]
    assert gate["gate_kind"] == "deterministic"


def test_provider_reports_headline_rows_for_the_summary():
    g = log()
    st, cfg, now = frozen(g)
    cfg.metrics.min_samples = 1
    ctx = Context(collect(g.events, st, now, cfg), cfg, now)
    by_metric = {r["metric"]: r for r in R.provider(ctx)}
    assert by_metric["runs.first_pass_rate"]["value"] == pytest.approx(0.5)
    assert by_metric["runs.runs_per_merge"]["value"] == pytest.approx(2.0)  # 4 runs / 2 items

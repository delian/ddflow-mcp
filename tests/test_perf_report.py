"""The perf report data layer (PM-report-data): one pure structure every renderer reads."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lanes_fixture import T0, Log
from metrics_fixture import finished, frozen

from ddflow.services.metrics.facts import collect
from ddflow.services.metrics.providers import Context
from ddflow.services.perf.report import build


def ctx_of(log: Log) -> Context:
    state, cfg, now = frozen(log)
    facts = collect(list(log.events), state, now=now, cfg=cfg)
    return Context(facts=facts, cfg=cfg, now=now, phase="", since=None)


def seeded() -> Log:
    log = Log()
    finished(log, "A", T0 + 0 * 3600, 2)
    finished(log, "B", T0 + 3 * 3600, 1)
    finished(log, "C", T0 + 5 * 3600, 4)
    log.add("gate.started", "A", "a1", T0 + 60, gate="ci")
    log.add("gate.passed", "A", "a1", T0 + 360, gate="ci", by="a1", evidence={"elapsed_s": 300})
    return log


def test_every_computable_section_is_present_and_numbered():
    report = build(ctx_of(seeded()), minimum=1)
    assert report.gates  # gate time + failure rate rows
    assert report.cycle["items"] == 3  # three completed items
    # Every number carries an n (or is named not collected).
    assert all("n" in g for g in report.gates)


def test_the_not_collected_sections_name_their_source_task():
    report = build(ctx_of(seeded()), minimum=1)
    assert report.not_collected["test_suite_size"] == "PM-test-durations"
    assert report.not_collected["complexity"] == "PM-complexity"
    assert (
        report.not_collected["network"] == "not collected by decision (D-perf-metrics-answers .8)"
    )


def test_a_report_is_deterministic_for_the_same_log():
    assert build(ctx_of(seeded()), minimum=1) == build(ctx_of(seeded()), minimum=1)


def test_under_the_minimum_a_number_is_none_not_zero():
    report = build(ctx_of(seeded()), minimum=50)  # far above the samples present
    assert all(g["p50_s"] is None for g in report.gates)
    assert report.cycle["p50_s"] is None


def test_the_model_and_reviewer_comparison_is_always_present_with_n():
    report = build(ctx_of(seeded()), minimum=1)
    assert report.find("models")  # the per-model comparison D-perf-metrics-answers mandates
    assert report.find("reviewers")
    assert all("n" in row for row in report.find("models"))


def test_a_zero_minimum_is_respected_not_treated_as_missing():
    report = build(ctx_of(seeded()), minimum=0)  # two durations must still yield a percentile
    assert any(g["p50_s"] is not None for g in report.gates)

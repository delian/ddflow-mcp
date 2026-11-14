"""Outcome-table shares: passed and partial rates, untimed gates, and the swarm rename.

``WF-metrics-pass-partial-share``: the outcome table once printed only failure, skip and
unavailable, so a ci gate read "fail 45% skip 1% unavailable 17%" while the true split was
passed 1.3% and partial 35% -- the dominant verdict was invisible in the headline report.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lanes_fixture import T0, Log
from metrics_fixture import frozen, twelve

from ddflow.services import metrics as M


def run(log: Log, **kw):
    st, cfg, now = frozen(log)
    for k, v in kw.pop("knobs", {}).items():
        setattr(cfg.metrics, k, v)
    return M.run(log.events, st, cfg, kw.pop("now", now), **kw)


def ci_log(n_passed: int = 1, n_partial: int = 9) -> Log:
    """``n_passed`` passed + ``n_partial`` partial ``ci`` outcomes, one completed item each."""
    log = Log()
    for i, verdict in enumerate(["passed"] * n_passed + ["partial"] * n_partial):
        tid = f"T{i:02d}"
        log.task(tid, f"f{i}.py")
        log.claim("a1", tid, T0 + i * 3600, f"f{i}.py")
        log.add(f"gate.{verdict}", tid, "a1", T0 + i * 3600 + 100, gate="ci", by="a1", evidence={})
        log.complete("a1", tid, T0 + i * 3600 + 600)
    return log


def test_outcome_rows_report_pass_and_partial_rates():
    """The falsifier: 1 passed + 9 partial reads as 10% and 90%, not as an omission."""
    by_gate = {r["group"]: r for r in run(ci_log()).metrics["outcomes"]}
    ci = by_gate["ci"]
    assert ci["passed"] == 1 and ci["partial"] == 9
    assert ci["pass_rate"] == pytest.approx(0.1)
    assert ci["partial_rate"] == pytest.approx(0.9)


def test_pass_and_partial_are_shares_of_every_verdict_not_just_passed_partial():
    """The pass/partial share is over ALL verdicts, so a viewer reads the true split. A
    two-verdict group cannot tell a correct ``passed/total`` from a wrong
    ``passed/(passed+partial)``: with failed outcomes in the group they disagree (5% and
    45%, not 10% and 90%)."""
    log = Log()
    i = 0
    for verdict, n in (
        ("passed", 1),
        ("partial", 9),
        ("failed", 10),
        ("skipped", 1),
        ("unavailable", 1),
    ):
        for _ in range(n):
            tid = f"T{i:02d}"
            log.task(tid, f"f{i}.py")
            log.claim("a1", tid, T0 + i * 3600, f"f{i}.py")
            log.add(
                f"gate.{verdict}", tid, "a1", T0 + i * 3600 + 100, gate="ci", by="a1", evidence={}
            )
            log.complete("a1", tid, T0 + i * 3600 + 600)
            i += 1
    ci = {r["group"]: r for r in run(log).metrics["outcomes"]}["ci"]
    assert ci["pass_rate"] == pytest.approx(1 / 22)
    assert ci["partial_rate"] == pytest.approx(9 / 22)


def test_summary_text_carries_pass_and_partial():
    ans = run(ci_log())
    assert "pass 10%" in ans.text
    assert "partial 90%" in ans.text


def test_time_per_gate_lists_untimed_as_attested():
    log = Log()
    log.task("C", "c.py")
    log.claim("a1", "C", T0, "c.py")
    log.add("gate.passed", "C", "a1", T0 + 100, gate="ci", by="a1", evidence={})  # untimed
    ans = run(log, knobs={"min_samples": 1})
    assert "not timed (attested): 1" in ans.text


def test_swarm_rework_metric_is_published_as_review_rework_rate():
    rows = {r["metric"]: r for r in run(twelve()).metrics["providers"]}
    assert rows["swarm.review_rework_rate"]["n"] == 12
    assert rows["swarm.rework_rate"]["n"] == 12  # one-release alias
    assert rows["swarm.rework_rate"]["value"] == rows["swarm.review_rework_rate"]["value"]

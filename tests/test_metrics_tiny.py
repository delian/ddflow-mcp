"""`ddflow metrics tiny`: the tiny-fix fast-track scoreboard, on a seeded log."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lanes_fixture import T0, Log
from metrics_fixture import frozen

from ddflow.services import metrics as M


def seeded() -> Log:
    """Three completed items of three sizes, two triaged findings and one later bug.

    A is tiny-cold (5 changed lines), later hit by a bug. B is small (30 lines), carries a
    confirmed MEDIUM finding. C is large (400 lines). Each has a timed rubber_duck and a
    merge event, so tiny-cold, gate seconds, finding rate and later-bug-by-bucket are all cut.
    """
    log = Log()
    log.add("phase.added", "P1", "lead", T0 - 2000, title="p1", body="")
    for tid in ("A", "B", "C"):
        log.add("task.added", tid, "lead", T0 - 1000, title=tid, body="", parent="P1")

    def item(tid, lines, magic):
        log.claim("a1", tid, T0 + magic, "f.py")
        log.add("gate.started", tid, "a1", T0 + magic + 60, gate="rubber_duck")
        log.add(
            "gate.passed",
            tid,
            "a1",
            T0 + magic + 300,
            gate="rubber_duck",
            by="r1",
            evidence={"model": "m", "elapsed_s": 200, "diff_stat": {"insertions": lines, "deletions": 0}},
        )  # fmt: skip
        log.add("worktree.merged", tid, "a1", T0 + magic + 400, sha="abc", branch="b")
        log.complete("a1", tid, T0 + magic + 500)
        log.release("a1", tid, T0 + magic + 501)

    item("A", 5, 0)
    item("B", 30, 1000)
    item("C", 400, 2000)
    log.add(
        "review.triaged", "A", "a1", T0 + 320, gate="rubber_duck", finding=1,
        digest="a" * 8, verdict="confirmed", severity="HIGH", probe="test_x", title="t", location="f.py",
    )  # fmt: skip
    log.add(
        "review.triaged", "A", "a1", T0 + 330, gate="rubber_duck", finding=2,
        digest="b" * 8, verdict="refuted", severity="LOW", probe="test_y", title="t", location="f.py",
    )  # fmt: skip
    log.add(
        "review.triaged", "B", "a1", T0 + 1300, gate="rubber_duck", finding=1,
        digest="c" * 8, verdict="confirmed", severity="MEDIUM", probe="test_z", title="t", location="g.py",
    )  # fmt: skip
    log.add("bug.found", "some-bug", "a2", T0 + 3000, item="A")
    return log


def run(log: Log):
    state, cfg, now = frozen(log)
    cfg.metrics.min_samples = 1
    return M.run(log.events, state, cfg, now, view="tiny")


def test_tiny_cold_counts_completed_items_under_the_line_limit():
    ans = run(seeded())
    tc = ans.metrics["tiny_cold"]
    assert tc["count"] == 1 and tc["total"] == 3 and tc["unmeasured"] == 0
    assert tc["rate"] == pytest.approx(1 / 3)
    assert "source-line limit" in tc["scope"]


def test_a_later_bug_is_counted_against_its_size_bucket_only():
    ans = run(seeded())
    by_size = {r["bucket"]: r for r in ans.metrics["by_size"]}
    assert by_size["tiny-cold (<=10 lines)"]["later"] == 1
    assert by_size["tiny-cold (<=10 lines)"]["later_bug_rate"] == pytest.approx(1.0)
    assert by_size["small (<=50 lines)"]["later"] == 0
    assert by_size["large (>300 lines)"]["later"] == 0


def test_confirmed_high_or_medium_findings_are_counted_not_low_or_refuted():
    ans = run(seeded())
    fr = ans.metrics["finding_rate"]
    assert fr["triaged"] == 3
    assert (
        fr["confirmed_high_medium"] == 2
    )  # A HIGH confirmed + B MEDIUM confirmed, not A's LOW refuted
    assert fr["rate"] == pytest.approx(2 / 3)


def test_gate_seconds_are_cut_per_gate():
    ans = run(seeded())
    rows = {r["gate"]: r for r in ans.metrics["gate_seconds"]}
    assert rows["rubber_duck"]["n"] == 3
    assert rows["rubber_duck"]["p50_s"] == pytest.approx(240)  # started +60, passed +300


def test_the_figures_the_log_cannot_support_are_named_not_measured():
    ans = run(seeded())
    assert "same_file_refix" in ans.metrics["not_measured"]
    assert "bundle_savings" in ans.metrics["not_measured"]
    assert "tiny_cold_files" in ans.metrics["not_measured"]


def test_an_unmeasured_size_is_not_counted_as_failing_the_tiny_limit():
    log = Log()
    log.add("phase.added", "P1", "lead", T0 - 2000, title="p1", body="")
    log.add("task.added", "A", "lead", T0 - 1000, title="A", body="", parent="P1")
    log.claim("a1", "A", T0, "f.py")
    log.add("gate.started", "A", "a1", T0 + 60, gate="rubber_duck")
    log.add(
        "gate.passed", "A", "a1", T0 + 300, gate="rubber_duck", by="a1", evidence={}
    )  # no diff_stat
    log.complete("a1", "A", T0 + 500)
    log.release("a1", "A", T0 + 501)
    ans = run(log)
    tc = ans.metrics["tiny_cold"]
    assert tc["total"] == 0  # its size was never measured, so it is not a failed limit
    assert tc["unmeasured"] == 1
    assert tc["rate"] is None  # no measured item behind the figure


def test_the_view_reports_over_cli_and_mcp():
    ans = run(seeded())
    assert ans.ok
    assert "tiny-fix fast track" in ans.text
    # The MCP surface serves the same view name as the CLI.
    assert "tiny" in M.VIEWS


def test_the_limits_are_fasttrack_knobs():
    from ddflow.config import Config

    cfg = Config()
    assert cfg.fasttrack.tiny_cold_max_source_lines == 10

"""`ddflow metrics batch`: the size-bucket tables, on a seeded log with known sizes and runs."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lanes_fixture import T0, Log
from metrics_fixture import frozen

from ddflow.services import metrics as M

R = "rubber_duck"


def seeded() -> Log:
    """Two finite items of very different size: A (~30 lines, one confirmed finding) and B
    (~1300 lines, two findings, one reference), plus a gate-lock wait on A's review."""
    log = Log()
    log.add("phase.added", "P1", "lead", T0 - 2000, title="p1", body="")
    log.add("task.added", "A", "lead", T0 - 1000, title="A", body="", parent="P1")
    log.add("task.added", "B", "lead", T0 - 1000, title="B", body="", parent="P1")

    def size(item: str) -> dict:
        return {
            "A": {"insertions": 20, "deletions": 10},
            "B": {"insertions": 800, "deletions": 500},
        }[item]

    for item in ("A", "B"):
        log.claim("a1", item, T0 + 600)
        log.add("gate.started", item, "a1", T0 + 900, gate=R)
        log.add(
            "gate.passed",
            item,
            "a1",
            T0 + 1100,
            gate=R,
            by="r1",
            evidence={
                "model": "m-alpha",
                "elapsed_s": 200,
                "waited_s": 30,
                "diff_stat": size(item),
            },
        )
        log.complete("a1", item, T0 + 1200)
        log.release("a1", item, T0 + 1201)
    log.add("review.triaged", "A", "a1", T0 + 1150, gate=R, severity="MEDIUM", verdict="confirmed")
    log.add("review.triaged", "B", "a1", T0 + 1500, gate=R, severity="LOW", verdict="confirmed")
    log.add("review.triaged", "B", "a1", T0 + 1501, gate=R, severity="LOW", verdict="refuted")
    return log


def run(log: Log, **kw):
    st, cfg, now = frozen(log)
    cfg.metrics.min_samples = 1
    return M.run(log.events, st, cfg, now, view="batch", **kw)


def bucket(ans, name: str) -> dict:
    (b,) = [b for b in ans.metrics["buckets"] if b["bucket"] == name]
    return b


def test_items_are_bucketed_by_their_recorded_changed_lines():
    ans = run(seeded())
    assert bucket(ans, "< 50")["items"] == 1
    assert bucket(ans, "1000+")["items"] == 1


def test_review_runs_are_counted_per_bucket():
    ans = run(seeded())
    small = bucket(ans, "< 50")
    assert small["runs"] == 1
    assert small["elapsed"]["p50_s"] == pytest.approx(200)
    assert small["lock_wait"]["p50_s"] == pytest.approx(30)


def test_findings_per_bucket_split_confirmed_and_refuted():
    ans = run(seeded())
    big = bucket(ans, "1000+")
    assert big["confirmed"] == 1
    assert big["refuted"] == 1
    assert big["findings"] == 2
    assert big["findings_per_run"] == pytest.approx(2.0)  # two findings over one run
    assert bucket(ans, "< 50")["runs_with_finding"] == pytest.approx(1.0)


def test_an_unknown_size_is_not_counted_as_small():
    """An item whose review evidence never named a changed-line figure is unknown size."""
    log = Log()
    log.add("task.added", "U", "lead", T0 - 1000, title="u", body="")
    log.claim("a1", "U", T0 + 600)
    log.add("gate.started", "U", "a1", T0 + 900, gate=R)
    log.add("gate.passed", "U", "a1", T0 + 1000, gate=R, by="r1", evidence={"elapsed_s": 100})
    log.complete("a1", "U", T0 + 1100)
    log.release("a1", "U", T0 + 1101)
    ans = run(log)
    assert bucket(ans, "(unknown size)")["items"] == 1
    assert bucket(ans, "< 50")["items"] == 0

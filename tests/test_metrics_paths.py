"""`ddflow metrics paths`: the workflow-path fold, on a seeded log with concurrent gates."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lanes_fixture import T0, Log
from metrics_fixture import frozen

from ddflow.services import metrics as M
from ddflow.core import clock


def seeded() -> Log:
    """A completed item A whose rubber_duck and critic ran CONCURRENTLY (overlapping 100 s),
    plus a record gate (research). The union of the two 100 s processes is 150 s, the sum is
    200 s -- so `process_union_s` is 150 and `process_sum_s` is 200 (the comment in the task
    that `metrics stages` counts concurrent gates twice)."""
    log = Log()
    log.add("phase.added", "P1", "lead", T0 - 2000, title="p1", body="")
    log.add("task.added", "A", "lead", T0 - 1000, title="A", body="", parent="P1")
    log.claim("a1", "A", T0, "f.py")
    log.add("gate.passed", "A", "a1", T0 + 100, gate="research", by="a1")  # a record step
    log.add("gate.started", "A", "a1", T0 + 900, gate="rubber_duck")
    log.add("gate.started", "A", "a1", T0 + 950, gate="critic")
    log.add(
        "gate.passed", "A", "a1", T0 + 1000, gate="rubber_duck", by="r1",
        evidence={"model": "m", "elapsed_s": 100, "round": 1, "tree_sha": "abc123"},
    )  # fmt: skip
    log.add(
        "gate.passed", "A", "a1", T0 + 1050, gate="critic", by="r2",
        evidence={"model": "m", "elapsed_s": 100, "round": 1, "tree_sha": "abc123"},
    )  # fmt: skip
    log.add("worktree.merged", "A", "a1", T0 + 1200, sha="abc", branch="b")
    log.complete("a1", "A", T0 + 1300)
    log.release("a1", "A", T0 + 1301)
    return log


def run(log: Log):
    state, cfg, now = frozen(log)
    cfg.metrics.min_samples = 1
    return M.run(log.events, state, cfg, now, view="paths")


def test_a_record_gate_is_a_record_step_and_a_timed_review_a_process_step():
    ans = run(seeded())
    (path,) = ans.metrics["paths"]
    by_gate = {st["gate"]: st for st in path["steps"]}
    assert by_gate["research"]["step"] == "record"
    assert by_gate["rubber_duck"]["step"] == "process"
    assert by_gate["critic"]["step"] == "process"


def test_concurrent_process_windows_are_a_union_not_a_sum():
    ans = run(seeded())
    (path,) = ans.metrics["paths"]
    assert path["process_sum_s"] == pytest.approx(200)  # rubber_duck 100 + critic 100
    assert path["process_union_s"] == pytest.approx(150)  # overlap 50 s: 100..150 gone once


def test_the_review_round_and_tree_identity_reach_the_path():
    ans = run(seeded())
    (path,) = ans.metrics["paths"]
    by_gate = {st["gate"]: st for st in path["steps"]}
    assert by_gate["rubber_duck"]["round"] == 1
    assert by_gate["rubber_duck"]["tree_sha"] == "abc123"


def test_the_union_correction_is_reported():
    ans = run(seeded())
    u = ans.metrics["process_union"]
    assert u["union"]["p50_s"] == pytest.approx(150)
    assert u["saved_by_union"]["p50_s"] == pytest.approx(50)
    assert "paths" in M.VIEWS

"""Gate yield, co-failure and same-input analysis (WP-gate-yield)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lanes_fixture import T0, Log

from ddflow.core import gateyield as GY
from ddflow.services.metrics.facts import collect


def facts_of(log: Log):
    from ddflow.config import Config
    from ddflow.core.model import fold

    state = fold(list(log.events), strict=False)
    return collect(log.events, state, now=T0 + 10_000, cfg=Config())


def seeded() -> Log:
    log = Log()
    log.add("phase.added", "P1", "lead", T0 - 2000, title="p1", body="")
    for tid in ("A", "B"):
        log.add("task.added", tid, "lead", T0 - 1000, title=tid, body="", parent="P1")
        log.claim("a1", tid, T0, "f.py")

    # A: rubber_duck FAILED then passed (first run failed); critic passed. A later bug -> rework.
    log.add(
        "gate.failed",
        "A",
        "a1",
        T0 + 100,
        gate="rubber_duck",
        by="r1",
        evidence={"elapsed_s": 100, "tree_sha": "t1"},
    )
    log.add("gate.started", "A", "a1", T0 + 200, gate="critic")
    log.add(
        "gate.passed",
        "A",
        "a1",
        T0 + 300,
        gate="rubber_duck",
        by="r1",
        evidence={"elapsed_s": 100, "tree_sha": "t1"},
    )
    log.add(
        "gate.passed",
        "A",
        "a1",
        T0 + 300,
        gate="critic",
        by="r2",
        evidence={"elapsed_s": 50, "tree_sha": "t1"},
    )
    log.add(
        "review.triaged",
        "A",
        "a1",
        T0 + 320,
        gate="critic",
        finding=1,
        digest="d" * 8,
        verdict="confirmed",
        severity="HIGH",
    )
    log.complete("a1", "A", T0 + 500)
    log.release("a1", "A", T0 + 501)
    log.add("bug.found", "b1", "a2", T0 + 900, item="A")

    # B: rubber_duck passed (first run passed); critic FAILED. Different tree.
    log.add(
        "gate.passed",
        "B",
        "a1",
        T0 + 150,
        gate="rubber_duck",
        by="r1",
        evidence={"elapsed_s": 10, "tree_sha": "t2"},
    )
    log.add(
        "gate.failed",
        "B",
        "a1",
        T0 + 200,
        gate="critic",
        by="r2",
        evidence={"elapsed_s": 20, "tree_sha": "t2"},
    )
    log.complete("a1", "B", T0 + 400)
    log.release("a1", "B", T0 + 401)

    # C: BOTH rubber_duck and critic failed (co-failure).
    log.add("task.added", "C", "lead", T0 - 1000, title="C", body="", parent="P1")
    log.claim("a1", "C", T0, "f.py")
    log.add(
        "gate.failed",
        "C",
        "a1",
        T0 + 150,
        gate="rubber_duck",
        by="r1",
        evidence={"elapsed_s": 10, "tree_sha": "t3"},
    )
    log.add(
        "gate.failed",
        "C",
        "a1",
        T0 + 200,
        gate="critic",
        by="r2",
        evidence={"elapsed_s": 20, "tree_sha": "t3"},
    )
    log.complete("a1", "C", T0 + 400)
    log.release("a1", "C", T0 + 401)
    return log


def test_first_run_pass_rate_counts_the_first_verdict_only(log=None):
    rows = {r["gate"]: r for r in GY.build(facts_of(seeded()), minimum=1)["gates"]}
    rd = rows["rubber_duck"]
    assert rd["first_run_pass_rate"] == pytest.approx(1 / 3)  # A failed, B passed, C failed
    assert rd["ever_failed_share"] == pytest.approx(2 / 3)


def test_cost_per_confirmed_finding_uses_only_confirmed():
    rows = {r["gate"]: r for r in GY.build(facts_of(seeded()), minimum=1)["gates"]}
    assert rows["critic"]["cost_per_confirmed_finding_s"] == pytest.approx(
        90 / 1
    )  # 50+20+20 elapsed / 1 confirmed


def test_co_failure_phi_builds_the_two_by_two():
    cf = GY.co_failure(facts_of(seeded()), "rubber_duck", "critic", minimum=1)
    assert cf["both_failed"] == 1 and cf["a_only"] == 1 and cf["b_only"] == 1 and cf["neither"] == 0
    assert cf["phi"] == pytest.approx(-0.5)  # (1*0-1*1)/sqrt(2*1*2*1)


def test_same_tree_groups_name_gates_on_one_tree():
    groups = GY.same_tree_groups(facts_of(seeded()))
    by_tree = {g["tree_sha"]: g for g in groups}
    assert set(by_tree["t1"]["runs"]) == {"rubber_duck", "critic"}
    assert set(by_tree["t2"]["runs"]) == {"rubber_duck", "critic"}
    assert len(groups) == 3  # t1, t2, t3 each saw rubber_duck + critic on one tree


def test_rework_loops_name_a_completed_item_hit_by_a_later_bug():
    loops = GY.rework_loops(facts_of(seeded()))
    assert [loop["item"] for loop in loops] == ["A"]

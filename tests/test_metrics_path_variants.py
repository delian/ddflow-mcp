"""The path variants (WP-path-variants): directly-follows, variants, overlap, superseded."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lanes_fixture import T0, Log

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.services.metrics.facts import collect
from ddflow.services.metrics.path_variants import (
    build,
    directly_follows,
    out_of_order,
    superseded_passes,
    variants,
)
from ddflow.services.metrics.providers import Context


def ctx_of(log: Log) -> Context:
    state = fold(list(log.events), strict=False)
    facts = collect(log.events, state, now=T0 + 10_000, cfg=Config())
    return Context(facts=facts, cfg=Config(), now=T0 + 10_000, phase="", since=None)


def seeded() -> Log:
    log = Log()
    log.add("phase.added", "P1", "lead", T0 - 2000, title="p1", body="")
    for tid in ("A", "B"):
        log.add("task.added", tid, "lead", T0 - 1000, title=tid, body="", parent="P1")
        log.claim("a1", tid, T0, "f.py")
    # A: research -> rubber_duck -> critic -> merge (canonical)
    log.add("gate.passed", "A", "a1", T0 + 100, gate="research", by="a1")
    log.add(
        "gate.passed", "A", "a1", T0 + 300, gate="rubber_duck", by="r1", evidence={"tree_sha": "t1"}
    )
    log.add("gate.passed", "A", "a1", T0 + 400, gate="critic", by="r2", evidence={"tree_sha": "t1"})
    log.complete("a1", "A", T0 + 500)
    log.release("a1", "A", T0 + 501)
    # B: critic BEFORE rubber_duck (out of order), then a superseded pass
    log.add("gate.passed", "B", "a1", T0 + 150, gate="critic", by="r1", evidence={"tree_sha": "t2"})
    log.add(
        "gate.passed", "B", "a1", T0 + 200, gate="rubber_duck", by="r2", evidence={"tree_sha": "t3"}
    )
    log.complete("a1", "B", T0 + 400)
    log.release("a1", "B", T0 + 401)
    return log


def test_directly_follows_counts_a_gate_pair_and_its_gap(log=None):
    rows = {r["a"]: r for r in directly_follows(ctx_of(seeded()))}
    assert rows["rubber_duck"]["n"] == 1  # only A goes rubber_duck -> critic
    assert rows["critic"]["n"] == 1  # B goes critic -> rubber_duck


def test_a_gate_run_before_an_earlier_canonical_step_is_out_of_order():
    ooo = out_of_order(ctx_of(seeded()))
    assert any(
        r["item"] == "B" and r["gate"] == "critic" for r in ooo
    )  # critic ran before rubber_duck
    assert not any(r["item"] == "A" for r in ooo)


def test_a_merged_lane_that_started_earlier_is_not_a_later_prerequisite():
    # rules@+1 and research@+10 are ONE merged lane; critic@+5 ran after that lane had already
    # started (rules), so it did not run ahead of it -- only a lane, not a gate, is a step.
    log = Log()
    log.add("phase.added", "P1", "lead", T0 - 2000, title="p1", body="")
    log.add("task.added", "A", "lead", T0 - 1000, title="A", body="", parent="P1")
    log.claim("a1", "A", T0, "f.py")
    log.add("gate.passed", "A", "a1", T0 + 1, gate="rules", by="a1")
    log.add("gate.passed", "A", "a1", T0 + 5, gate="critic", by="r1")
    log.add("gate.passed", "A", "a1", T0 + 10, gate="research", by="a1")
    ooo = out_of_order(ctx_of(log))
    assert not any(r["gate"] == "critic" for r in ooo)


def test_a_pass_superseded_by_a_later_tree_change_is_listed():
    # B's critic passed on tree t2; rubber_duck then ran on t3, so the critic pass is
    # superseded (the tree changed after it). A's rubber_duck/critic share t1, so neither is.
    superseded = superseded_passes(ctx_of(seeded()))
    assert any(r["item"] == "B" and r["gate"] == "critic" for r in superseded)
    assert not any(r["item"] == "A" for r in superseded)


def test_build_names_the_cli_and_mcp_next_call():
    out = build(ctx_of(seeded()))
    assert out["next"]["cli"] == "ddflow paths"
    assert out["next"]["mcp"] == "ddflow_paths"
    assert out["variants"] and out["directly_follows"]


def test_overlap_is_union_versus_sum_and_never_exceeds_the_sum():
    ov = build(ctx_of(seeded()))["overlap"]
    assert ov["n_items"] == 2
    assert ov["saved_s"] >= 0
    assert ov["union_s"] + ov["saved_s"] == ov["sum_s"]
    assert ov["union_s"] <= ov["sum_s"] + 1e-9
    assert ov["burst_share"] is None or ov["burst_share"] <= 1


def test_variants_collapse_consecutive_reruns_into_one_step():
    # A's sequence research -> rubber_duck -> critic; B's critic -> rubber_duck.
    seen = variants(ctx_of(seeded()))
    a = next(v for v in seen if v["sequence"] == ["research", "rubber_duck", "critic"])
    b = next(v for v in seen if v["sequence"] == ["critic", "rubber_duck"])
    assert a["n"] == 1 and b["n"] == 1

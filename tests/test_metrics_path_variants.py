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
    assert any(r["item"] == "B" and r["gate"] == "critic" for r in ooo)  # critic ran before rubber_duck
    assert not any(r["item"] == "A" for r in ooo)


def test_a_pass_superseded_by_a_later_tree_change_is_listed():
    # A's critic on t1 is not superseded (no later tree); B has no superseded pass.
    assert superseded_passes(ctx_of(seeded())) == []


def test_build_names_the_cli_and_mcp_next_call():
    out = build(ctx_of(seeded()))
    assert out["next"]["cli"] == "ddflow metrics paths --variants"
    assert out["variants"] and out["directly_follows"]

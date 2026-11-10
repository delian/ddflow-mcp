"""The workflow-path effect block (WP-path-effect): baseline, current, and before/after."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lanes_fixture import T0, Log
from metrics_fixture import H, twelve
from pytest import approx

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.services.metrics import paths as path_metrics
from ddflow.services.metrics.facts import collect
from ddflow.services.metrics.providers import Context
from ddflow.services.perf.path_effect import (
    BASELINES,
    baseline,
    current,
    effect,
    has_figures,
    parked_share,
    sequential_review_share,
    superseded_pass_hours,
    text,
)

NOW = T0 + 100 * H


def ctx_of(log: Log, now: float = NOW) -> Context:
    state = fold(list(log.events), strict=False)
    facts = collect(log.events, state, now=now, cfg=Config())
    return Context(facts=facts, cfg=Config(), now=now, phase="", since=None)


def claimdone(log: Log, tid: str, done: float, globs: str = "f.py") -> None:
    log.claim("a1", tid, T0, globs)
    log.complete("a1", tid, done)
    log.release("a1", tid, done + 1)


def one_pair(log: Log, tid: str) -> None:
    """One rubber_duck -> critic pair on one tree, SEQUENTIAL (timed, non-overlapping)."""
    log.add(
        "gate.passed",
        tid,
        "a1",
        T0 + 100,
        gate="rubber_duck",
        by="r1",
        evidence={"tree_sha": "t1", "elapsed_s": 60},
    )
    log.add(
        "gate.passed",
        tid,
        "a1",
        T0 + 300,
        gate="critic",
        by="r2",
        evidence={"tree_sha": "t1", "elapsed_s": 60},
    )


def test_baseline_falls_back_to_canonical_for_an_unknown_env_key():
    assert baseline(None) == BASELINES[""]
    assert baseline("no-such-class") == BASELINES[""]
    assert baseline("")["process_share"] == approx(BASELINES[""]["process_share"])
    # the canonical numbers are the R-path-merge ones the task pins
    b = baseline(None)
    assert b["process_share"] > 0 and b["process_share"] < 1
    assert b["parked_share"] == 0.22


def test_current_computes_gate_hours_cycle_and_process_share():
    log = twelve()
    ctx = ctx_of(log)
    cur = current(ctx)
    assert cur["n_completed_items"] == 12
    paths = path_metrics.build(ctx)["paths"]
    union = sum(p["process_union_s"] for p in paths)
    cycle = sum(s.cycle_s for s in ctx.facts.spans.values() if s.cycle_s is not None)
    assert cur["gate_hours_per_merged_s"]["value"] == approx(union / 12)
    assert cur["process_share"]["value"] == approx(union / cycle)
    assert cur["claim_to_merge_p50_s"]["value"] is not None
    assert cur["claim_to_merge_p90_s"]["value"] is not None
    # the burst-corrected per-item hours can never exceed the summed wall, and the share
    # is a fraction of claim-to-merge, so it stays in (0, 1]
    assert 0 < cur["process_share"]["value"] <= 1


def test_sequential_review_share_classifies_combined_concurrent_sequential():
    log = Log()
    for tid in ("A", "B", "C"):
        log.task(tid)
    # A: sequential (rubber_duck [40,100], critic [240,300] do not overlap)
    claimdone(log, "A", T0 + 500)
    one_pair(log, "A")
    # B: concurrent (rubber_duck [100,200], critic [150,250] overlap)
    claimdone(log, "B", T0 + 500)
    log.add(
        "gate.passed",
        "B",
        "a1",
        T0 + 200,
        gate="rubber_duck",
        by="r1",
        evidence={"tree_sha": "t1", "elapsed_s": 100},
    )
    log.add(
        "gate.passed",
        "B",
        "a1",
        T0 + 250,
        gate="critic",
        by="r2",
        evidence={"tree_sha": "t1", "elapsed_s": 100},
    )
    # C: combined (one command recorded both gates)
    claimdone(log, "C", T0 + 500)
    log.add(
        "gate.passed",
        "C",
        "a1",
        T0 + 100,
        gate="rubber_duck",
        by="r1",
        evidence={"tree_sha": "t1", "combined_gates": ["critic"]},
    )
    log.add(
        "gate.passed",
        "C",
        "a1",
        T0 + 100,
        gate="critic",
        by="r1",
        evidence={"tree_sha": "t1", "combined_gates": ["rubber_duck"]},
    )
    share = sequential_review_share(ctx_of(log), minimum=1)
    assert share["sequential"] == 1
    assert share["concurrent"] == 1
    assert share["combined"] == 1
    assert share["n"] == 3


def test_sequential_review_share_value_requires_the_sample_floor():
    log = Log()
    for n in range(10):
        tid = f"T{n:02d}"
        log.task(tid)
        claimdone(log, tid, T0 + 500)
        one_pair(log, tid)
    share = sequential_review_share(ctx_of(log), minimum=10)
    assert share["n"] == 10
    assert share["value"] == approx(1.0)  # every pair sequential
    thin = sequential_review_share(ctx_of(Log()), minimum=10)
    assert thin["value"] is None  # 0 pairs < floor: withheld, never zero


def test_superseded_pass_hours_sums_elapsed_of_superseded_passes():
    log = Log()
    log.task("A")
    claimdone(log, "A", T0 + 500)
    # ci passes on t1, then unit_tests passes on a LATER tree t2 -> the ci pass is superseded
    log.add(
        "gate.passed",
        "A",
        "a1",
        T0 + 100,
        gate="ci",
        by="a1",
        evidence={"tree_sha": "t1", "elapsed_s": 120},
    )
    log.add(
        "gate.passed",
        "A",
        "a1",
        T0 + 300,
        gate="unit_tests",
        by="a1",
        evidence={"tree_sha": "t2", "elapsed_s": 180},
    )
    # a control pass with no elapsed is not counted as hours, and one on the SAME tree is not superseded
    log.add("gate.passed", "A", "a1", T0 + 400, gate="ci", by="a1", evidence={"tree_sha": "t2"})
    got = superseded_pass_hours(ctx_of(log))
    assert got["count"] == 1
    assert got["seconds"] == approx(120.0)
    assert got["n_items"] == 1


def test_superseded_pass_hours_counts_an_untimed_superseded_pass():
    # a superseded pass that recorded NO elapsed_s still counts (a count, zero seconds), so a
    # window with a superseded pass is never reported n=0 as if none had occurred (grok/4.7).
    log = Log()
    log.task("A")
    claimdone(log, "A", T0 + 500)
    log.add("gate.passed", "A", "a1", T0 + 100, gate="ci", by="a1", evidence={"tree_sha": "t1"})
    log.add(
        "gate.passed", "A", "a1", T0 + 300, gate="unit_tests", by="a1", evidence={"tree_sha": "t2"}
    )
    got = superseded_pass_hours(ctx_of(log))
    assert got["count"] == 1
    assert got["seconds"] == 0.0


def test_superseded_pass_hours_is_a_true_zero_not_a_missing_measurement():
    log = Log()
    log.task("A")
    claimdone(log, "A", T0 + 500)
    log.add(
        "gate.passed",
        "A",
        "a1",
        T0 + 100,
        gate="ci",
        by="a1",
        evidence={"tree_sha": "t1", "elapsed_s": 120},
    )
    got = superseded_pass_hours(ctx_of(log))
    assert got["seconds"] == 0.0 and got["count"] == 0 and got["n_items"] == 1


def test_superseded_pass_hours_counts_only_completed_items():
    # an INCOMPLETE item's superseded pass is not attributed to the merged-item rate (grok/4.7)
    log = Log()
    log.task("DONE")
    claimdone(log, "DONE", T0 + 500)
    log.add(
        "gate.passed",
        "DONE",
        "a1",
        T0 + 100,
        gate="ci",
        by="a1",
        evidence={"tree_sha": "t1", "elapsed_s": 120},
    )
    log.task("HALF")
    log.claim("a1", "HALF", T0, "f.py")  # claimed but never completed
    log.add(
        "gate.passed",
        "HALF",
        "a1",
        T0 + 100,
        gate="ci",
        by="a1",
        evidence={"tree_sha": "t1", "elapsed_s": 3600},
    )
    log.add(
        "gate.passed",
        "HALF",
        "a1",
        T0 + 200,
        gate="unit_tests",
        by="a1",
        evidence={"tree_sha": "t2", "elapsed_s": 60},
    )
    got = superseded_pass_hours(ctx_of(log))
    assert got["n_items"] == 1  # only DONE completed
    assert got["count"] == 0  # HALF's superseded pass is not a merged-item cost
    assert got["seconds"] == 0.0


def test_parked_share_detects_an_idle_stretch_beyond_the_threshold():
    log = Log()
    log.task("PARK")
    # claimed, one gate, then nothing for 3 h (> PARKED_THRESHOLD_S), then milled -> parked
    log.claim("a1", "PARK", T0, "f.py")
    log.add("gate.passed", "PARK", "a1", T0 + 60, gate="research", by="a1")
    log.add(
        "gate.passed",
        "PARK",
        "a1",
        T0 + 3 * H + 60,
        gate="rubber_duck",
        by="r1",
        evidence={"elapsed_s": 30},
    )
    log.complete("a1", "PARK", T0 + 3 * H + 120)
    log.release("a1", "PARK", T0 + 3 * H + 121)
    assert parked_share(ctx_of(log), minimum=1)["parked"] == 1

    # a busy item with no 2h gap is NOT parked
    log3 = Log()
    log3.task("BUSY")
    log3.claim("a1", "BUSY", T0, "f.py")
    log3.add("gate.passed", "BUSY", "a1", T0 + 60, gate="research", by="a1")
    log3.add(
        "gate.passed",
        "BUSY",
        "a1",
        T0 + 600,
        gate="rubber_duck",
        by="r1",
        evidence={"elapsed_s": 30},
    )
    log3.complete("a1", "BUSY", T0 + 1200)
    log3.release("a1", "BUSY", T0 + 1201)
    assert parked_share(ctx_of(log3), minimum=1)["parked"] == 0


def test_effect_delta_is_current_minus_baseline_and_withheld_under_the_floor():
    cur = current(ctx_of(twelve()))
    eff = effect(ctx_of(twelve()))
    base = baseline(None)
    for row in eff["metrics"]:
        assert row["baseline"] == base[row["metric"]]
        if cur[row["metric"]]["value"] is not None:
            assert row["current"] == cur[row["metric"]]["value"]
            assert row["delta"] == approx(cur[row["metric"]]["value"] - base[row["metric"]])

    # under the floor the delta is withheld (never a fabricated comparison)
    thin = effect(ctx_of(Log()))
    assert all(r["current"] is None for r in thin["metrics"])
    assert all(r["delta"] is None for r in thin["metrics"])


def test_has_figures_is_false_when_nothing_computed():
    assert has_figures(effect(ctx_of(Log()))) is False
    assert has_figures(effect(ctx_of(twelve()))) is True


def test_text_renders_the_block_and_names_the_next_call():
    out = text(effect(ctx_of(twelve())))
    assert "workflow-path effect (vs R-path-merge baseline)" in out
    assert "next: ddflow perf report  |  ddflow_perf action=report" in out
    assert "claim-to-merge p50" in out

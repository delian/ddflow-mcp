"""ETA duration tables: each derived measure folded once from the log, never a zero when
missing, and never a number that the insufficient-data state cannot name."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lanes_fixture import T0, Log

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.services.metrics.facts import collect

from ddflow.services.eta import data as ED

H = 3600.0


def facts_of(log: Log, now: float | None = None):
    cfg = Config()
    st = fold(list(log.events), strict=False)
    return collect(list(log.events), st, now, cfg), cfg, st


def finished(log: Log, tid: str, claim: float, hours: float, *, globs: str = "") -> None:
    """Claim at ``claim``, done ``hours`` later: research passed, rubber_duck failed then
    passed, docs skipped (mirrors metrics_fixture.finished, so cycle and rounds are known)."""
    log.task(tid, globs)
    log.claim("a1", tid, claim, globs)
    log.add("gate.started", tid, "a1", claim + 60, gate="research")
    log.add("gate.passed", tid, "a1", claim + 360, gate="research", by="a1", evidence={})
    log.add(
        "gate.failed",
        tid,
        "a1",
        claim + hours * H / 2,
        gate="rubber_duck",
        by="r1",
        evidence={"model": "m-alpha", "elapsed_s": 300},
    )
    log.add(
        "gate.passed",
        tid,
        "a1",
        claim + hours * H - 60,
        gate="rubber_duck",
        by="r1",
        evidence={"model": "m-alpha", "elapsed_s": 200},
    )
    log.add(
        "gate.skipped",
        tid,
        "a1",
        claim + hours * H - 30,
        gate="docs",
        by="a1",
        reason="no user change",
    )
    log.complete("a1", tid, claim + hours * H)
    log.release("a1", tid, claim + hours * H + 1)


def test_cycle_times_are_first_claim_to_first_completion():
    log = Log()
    t = T0
    for n in range(1, 4):
        finished(log, f"T{n}", t, float(n), globs=f"f{n}.py")
        t += n * H + 60
    facts, _, _st = facts_of(log)
    table = ED.build(facts, Config().eta)
    assert [s for s in table.cycles.samples] == [pytest.approx(n * H) for n in (1, 2, 3)]
    assert table.cycles.n_samples == 3
    assert table.cycles.id_classes == {f"T{n}": "T" for n in (1, 2, 3)}


def test_gate_durations_pair_a_started_with_its_verdict_else_the_review_time():
    log = Log()
    finished(log, "T1", T0, 2.0, globs="a.py")
    table = ED.build(facts_of(log)[0], Config().eta)
    got = {t.name: t for t in table.gate_durations}
    assert got["research"].n_samples == 1
    assert got["research"].samples == [pytest.approx(300.0)]
    assert got["rubber_duck"].n_samples == 2  # failed (elapsed_s 300) + passed (elapsed_s 200)
    assert "docs" not in got  # skipped: no started, no timed verdict


def test_review_rounds_count_run_verdicts_of_a_review_gate():
    log = Log()
    finished(log, "R1", T0, 1.0, globs="r.py")
    table = ED.build(facts_of(log)[0], Config().eta)
    assert table.rounds.gate == "rubber_duck"
    assert table.rounds.samples == [2.0]  # failed + passed over the item's life
    assert table.rounds.samples_summary["n"] == 1


def test_no_review_run_gives_no_round_sample_not_a_zero():
    log = Log()
    log.task("N", "n.py")
    log.claim("a1", "N", T0, "n.py")
    log.complete("a1", "N", T0 + H)  # no review verdicts
    table = ED.build(facts_of(log)[0], Config().eta)
    assert table.rounds.gate == ""
    assert table.rounds.samples == []


def test_review_gate_tie_break_is_deterministic():
    """With both review gates carrying the same RAN count, the dominant gate must be the
    alphabetically-greatest review gate, deterministically -- the exact pair ``_review_gate``
    ties over when a log made the two gates equally common (no set-iteration coin flip)."""
    log = Log()
    for tid in ("A", "B"):
        log.task(tid, f"{tid.lower()}.py")
        log.claim("a1", tid, T0, f"{tid.lower()}.py")
        log.add("gate.passed", tid, "a1", T0 + 60, gate="rubber_duck", by="r1", evidence={})
        log.add("gate.passed", tid, "a1", T0 + 60, gate="critic", by="r1", evidence={})
        log.complete("a1", tid, T0 + H)
    table = ED.build(facts_of(log)[0], Config().eta)
    assert table.rounds.gate == "critic"  # ties broken leftmost-in-sorted order, deterministically
    assert table.rounds.samples == [1.0, 1.0]


def test_gate_durations_respect_the_rolling_window():
    """An item completed before the window floor contributes no gate-duration sample."""
    log = Log()
    finished(log, "OLD", T0 - 100 * 86400.0, 1.0, globs="old.py")
    finished(log, "NEW", T0, 1.0, globs="new.py")
    cfg = Config()
    cfg.eta.window_days = 1
    table = ED.build(facts_of(log)[0], cfg.eta)
    by_name = {t.name: t for t in table.gate_durations}
    assert by_name["research"].samples == [300.0]  # the NEW item only
    assert by_name["rubber_duck"].samples == [300.0, 200.0]


def test_lease_wait_is_first_wait_signal_to_first_claim():
    log = Log()
    log.task("W", "w.py")
    log.add("lease.waiting", "W", "w1", T0, at=T0 - 600, globs=["w.py"], holder="h1")
    log.claim("w1", "W", T0, "w.py")
    log.complete("w1", "W", T0 + H)
    table = ED.build(facts_of(log)[0], Config().eta)
    assert table.lease_waits.samples == [pytest.approx(600.0)]


def test_figures_under_min_samples_are_withheld_not_zero():
    log = Log()
    finished(log, "S1", T0, 1.0, globs="s.py")
    cfg = Config()
    cfg.eta.min_samples = 5
    table = ED.build(facts_of(log)[0], cfg.eta)
    assert table.cycles.n_samples == 1
    assert table.cycles.samples_summary["p50_s"] is None  # withheld, not 0
    assert table.cycles.samples_summary["n"] == 1
    assert "cycle_time" in table.insufficient


def test_insufficient_is_empty_when_enough_samples_exist():
    log = Log()
    t = T0
    for n in range(1, 6):
        finished(log, f"G{n}", t, 1.0, globs=f"g{n}.py")
        t += 2 * H
    cfg = Config()
    cfg.eta.min_samples = 5
    table = ED.build(facts_of(log)[0], cfg.eta)
    assert "cycle_time" not in table.insufficient
    assert table.cycles.samples_summary["p50_s"] is not None

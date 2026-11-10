"""The SLO table discriminates a good week from a bad week (OBS-slo-set falsifier).

The SLIs are derived from the existing metrics fold; the falsifier is that they would NOT
tell a good week from a bad one on the real log. These tests build both weeks event by
event, fold them, and check the table flags the bad week and leaves the good week alone --
and that the review-failure rate stays a SIGNAL (never a breach) and a thin week is
`n/a`, never a fabricated zero.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lanes_fixture import T0, Log

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.services.metrics.facts import collect
from ddflow.services.metrics.providers import Context
from ddflow.services.metrics.slo import assess

H = 3600.0


def _ctx(log: Log, now: float) -> Context:
    cfg = Config()
    events = list(log.events)
    state = fold(events, strict=False)
    return Context(collect(events, state, now, cfg), cfg, now, None, "")


def _gated(log: Log, tid: str, start: float, gate_secs: float, *, fail: bool = False) -> None:
    """One ci verdict with a known duration, so the fixed fold has a known p90."""
    log.add("gate.started", tid, "a1", start, gate="ci")
    log.add(
        "gate.failed" if fail else "gate.passed",
        tid,
        "a1",
        start + gate_secs,
        gate="ci",
        by="a1",
        evidence={"elapsed_s": gate_secs},
    )


def _good_week() -> Log:
    log = Log()
    t = T0
    for n in range(12):
        tid = f"G{n:02d}"
        log.add("task.added", tid, "lead", t - 10, title=f"g{n}", body="x", globs=[f"g{n}.py"])
        log.claim("a1", tid, t, f"g{n}.py")  # ~10s in the queue
        _gated(log, tid, t + 20, 50.0)
        log.complete("a1", tid, t + 600)
        log.release("a1", tid, t + 601)
        log.add(
            "worktree.merged",
            tid,
            "a1",
            t + 602,
            landed_before="b",
            landed_after="a",
            branch_head="h",
        )
        t += H
    return log


def _bad_week() -> Log:
    log = Log()
    t = T0
    for n in range(12):
        tid = f"B{n:02d}"
        log.add("task.added", tid, "lead", t - 2000, title=f"b{n}", body="x", globs=[f"b{n}.py"])
        log.claim("a1", tid, t, f"b{n}.py")  # ~2000s in the queue
        _gated(log, tid, t + 20, 700.0)  # gate p90 far over the 300s target
        log.complete("a1", tid, t + 3000)
        log.add(
            "worktree.merged",
            tid,
            "a1",
            t + 3002,
            landed_before="b",
            landed_after="a",
            branch_head="h",
        )
        t += H
    for tid in ("B00", "B01", "B02"):
        # a red-main bug within a day of the merge: CI false-red
        log.add("bug.found", tid, "a1", T0 + 3021, item=tid)
    for n in range(3):  # three never-released leases: stuck lanes over the 2 target
        log.claim("stuck", f"ST{n}", T0 + 30 * H, f"st{n}.py")
        log.add("task.added", f"ST{n}", "lead", T0 + 30 * H - 5, title="s", body="x", globs=[])
    return log


def test_the_slis_discriminate_a_good_week_from_a_bad_week():
    good = assess(_ctx(_good_week(), T0 + 13 * H))
    bad = assess(_ctx(_bad_week(), T0 + 40 * H))

    gate_p90_target = Config().slo.target_gate_p90_s

    good_gate = next(r for r in good["sli"] if r["name"] == "gate_p90")
    assert good_gate["value"] is not None and good_gate["value"] < gate_p90_target
    assert not good["breached"], good["breached"]

    bad_gate = next(r for r in bad["sli"] if r["name"] == "gate_p90")
    assert bad_gate["value"] is not None and bad_gate["value"] > gate_p90_target
    bad_queue = next(r for r in bad["sli"] if r["name"] == "queue_age")
    assert bad_queue["breached"] is True  # ~2000s in queue over the 600s target
    assert "gate_p90" in bad["breached"]


def test_only_queue_age_and_ci_false_red_get_a_burn_rate_view():
    data = assess(_ctx(_bad_week(), T0 + 40 * H))
    burning = {r["name"] for r in data["sli"] if r["burn"]}
    assert burning == {"queue_age", "ci_false_red_rate"}


def test_the_review_failure_rate_is_a_signal_never_a_breach():
    log = Log()
    t = T0
    for n in range(12):
        tid = f"R{n:02d}"
        log.add("task.added", tid, "lead", t - 5, title="r", body="x", globs=[])
        log.claim("a1", tid, t, "")
        for gate in ("critic", "rubber_duck"):
            log.add(
                "gate.failed", tid, "r1", t + 100, gate=gate, by="r1", evidence={"model": "m-other"}
            )
        log.complete("a1", tid, t + 300)
        log.release("a1", tid, t + 301)
        t += H
    row = next(r for r in assess(_ctx(log, t))["sli"] if r["name"] == "review_finding_rate")
    assert row["value"] is not None  # it is computed
    assert row["breached"] is None  # ... but never a breach
    assert "review_finding_rate" not in assess(_ctx(log, t))["breached"]


def test_a_figure_below_the_floor_is_none_not_a_zero():
    log = Log()
    t = T0
    for n in range(3):  # far fewer than [metrics].min_samples
        tid = f"T{n:02d}"
        log.add("task.added", tid, "lead", t - 5, title="t", body="x", globs=[])
        log.claim("a1", tid, t, "")
        _gated(log, tid, t + 20, 50.0)
        log.complete("a1", tid, t + 300)
        log.release("a1", tid, t + 301)
        t += H
    gate = next(r for r in assess(_ctx(log, t))["sli"] if r["name"] == "gate_p90")
    assert gate["value"] is None  # n<10, so unavailable, never 0


def test_the_targets_read_the_slo_knobs():
    cfg = Config()
    data = assess(_ctx(_bad_week(), T0 + 40 * H))
    by_name = {r["name"]: r["target"] for r in data["sli"]}
    assert by_name["gate_p90"] == cfg.slo.target_gate_p90_s
    assert by_name["queue_age"] == cfg.slo.target_queue_age_s
    assert by_name["ci_false_red_rate"] == cfg.slo.target_ci_false_red_rate

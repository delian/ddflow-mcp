"""`LC-measure`: the lane-wait figures over a seeded log with known durations."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lanes_fixture import T0, Log
from metrics_fixture import frozen

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.services import lanewait as LW

H = 3600.0


def seeded() -> Log:
    """A and B both queue a ci run behind the shared lock (waited 360 and 720 s), then
    complete; A is ready from filing (T0-1000) until its claim at T0, B until T0+1000."""
    log = Log()
    log.task("A", "a.py")
    log.claim("a1", "A", T0, "a.py")
    log.add("gate.started", "A", "a1", T0 + 100, gate="ci")
    log.add("gate.passed", "A", "a1", T0 + 400, gate="ci", by="a1", evidence={"waited_s": 360})
    log.complete("a1", "A", T0 + 500)
    log.release("a1", "A", T0 + 600)
    log.task("B", "b.py")
    log.claim("a1", "B", T0 + 1000, "b.py")
    log.add("gate.started", "B", "a1", T0 + 1100, gate="ci")
    log.add("gate.passed", "B", "a1", T0 + 1400, gate="ci", by="a1", evidence={"waited_s": 720})
    log.complete("a1", "B", T0 + 1500)
    log.release("a1", "B", T0 + 1600)
    return log


def run(log=None, now=None, **knobs):
    log = log or seeded()
    st, cfg, _ = frozen(log, now or T0 + 5 * H)
    cfg.metrics.min_samples = 1
    for k, v in knobs.items():
        setattr(cfg.metrics, k, v)
    return LW.lane_wait(log.events, st, cfg, now or T0 + 5 * H)


def test_gate_waits_are_lane_hours_by_gate():
    gw = run()["gate_wait_hours"]
    assert gw["ci"] == pytest.approx((360 + 720) / H)
    assert gw["total"] == pytest.approx(1080 / H)


def test_every_gate_that_queued_for_the_lock_is_counted():
    # a non-ci gate that queues for the host lock counts toward the total too: waited_s is
    # the shared-lock queue, whatever the gate is named, never filtered out.
    log = seeded()
    log.add(
        "gate.passed", "A", "a1", T0 + 300, gate="standards", by="a1",
        evidence={"waited_s": 180},
    )
    st, cfg, _ = frozen(log, T0 + 5 * H)
    cfg.metrics.min_samples = 1
    gw = LW.lane_wait(log.events, st, cfg, T0 + 5 * H)["gate_wait_hours"]
    assert gw["standards"] == pytest.approx(180 / H)
    assert gw["ci"] == pytest.approx(1080 / H)
    assert gw["total"] == pytest.approx((1080 + 180) / H)


def test_ci_lock_queue_is_the_ci_wait_stat():
    ci = run()["ci_lock_queue"]
    assert ci["n"] == 2 and ci["p50_s"] == pytest.approx(540) and ci["max_s"] == 720


def test_ready_to_claimed_is_the_queue_wait_stat():
    r = run()["ready_to_claimed"]
    # A waited 1000 s (filed at T0-1000, claimed T0); B waited 2000 s.
    assert r["n"] == 2 and r["p50_s"] == pytest.approx(1500) and r["max_s"] == 2000


def test_merged_uses_the_swarm_baseline_method():
    m = run()["merged"]
    assert m["merges"] == 2
    # wall = last completion (T0+1500) - first claim (T0) = 1500 s -> 4.8 per hour
    assert m["per_hour"] == pytest.approx(2 / (1500 / H))


def test_idle_reports_the_grid_and_a_working_lane():
    idle = run()["idle"]
    assert idle["samples"] >= 1
    assert idle["working_lane_samples"] >= 1  # a1 held A across the sampled window
    assert idle["utilisation"] is not None


def test_under_min_samples_every_figure_is_withheld_not_zero():
    log = seeded()
    st, cfg, now = frozen(log, T0 + 5 * H)  # default min_samples = 10
    d = LW.lane_wait(log.events, st, cfg, now)
    assert d["ready_to_claimed"]["p50_s"] is None
    assert d["ci_lock_queue"]["p50_s"] is None
    assert d["merged"]["per_hour"] is None
    assert "ready_to_claimed" in d["unavailable"] and "merged_per_hour" in d["unavailable"]


def test_no_gate_wait_is_zeroed():
    log = Log()
    log.task("A", "a.py")
    log.claim("a1", "A", T0, "a.py")
    log.complete("a1", "A", T0 + 600)
    st, cfg, now = frozen(log, T0 + 5 * H)
    cfg.metrics.min_samples = 1
    d = LW.lane_wait(log.events, st, cfg, now)
    assert d["gate_wait_hours"]["total"] == 0.0 and d["gate_wait_hours"]["shared"] == {}


def test_reporting_surface_renders_the_text():
    from ddflow.api.reporting import metrics_lanes as ML

    log = seeded()
    st, cfg, now = frozen(log, T0 + 5 * H)
    cfg.metrics.min_samples = 1
    data = ML.lane_wait_metrics(log, st, cfg, now, events=log.events)
    assert "lane wait" in data["text"]
    assert "ci lock queue" in data["text"]
    assert "merged:" in data["text"]
    assert data["gate_wait_hours"]["total"] == pytest.approx(1080 / H)

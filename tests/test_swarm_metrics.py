"""The swarm baseline: throughput, rework, cost proxy, critical path, before/after compare."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lanes_fixture import T0, Log
from metrics_fixture import NOW, H, twelve

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.services import metrics as M
from ddflow.services import swarm_metrics as S


def _run(log: Log, cfg: Config | None = None, **kw) -> dict:
    return S.baseline(log.events, fold(list(log.events), strict=False), cfg or Config(), NOW, **kw)


def test_twelve_items_throughput_and_chain():
    got = _run(twelve())
    assert got["merged"] == 12
    wall = sum(n * H + 60 for n in range(1, 13)) - 60
    assert got["throughput"]["wall_s"] == wall
    assert abs(got["throughput"]["per_hour"] - 12 / (wall / H)) < 1e-9
    cp = got["critical_path"]
    assert cp["wall_s"] == wall and cp["length"] == 1  # no needs: chains are single items
    assert cp["duration_s"] == 12 * H
    assert cp["slack_s"] == wall - 12 * H


def test_dependent_chain_weights_cycle_time():
    log = Log()
    t = T0
    for n, tid in enumerate(["A", "B", "C"], 1):
        log.task(tid, f"{tid}.py", needs="" if n == 1 else ["A", "B", "C"][n - 2])
        log.claim("a1", tid, t, f"{tid}.py")
        log.complete("a1", tid, t + n * H)
        log.release("a1", tid, t + n * H + 1)
        t += n * H + 10
    thin = _run(log)  # under min_samples
    assert thin["unavailable"]["throughput"].startswith("3 merged items")
    assert thin["critical_path"]["length"] is None and thin["critical_path"]["slack_s"] is None
    cfg = Config()
    cfg.metrics.min_samples = 1
    cp = _run(log, cfg)["critical_path"]
    assert cp["merged_chain"] == ["A", "B", "C"] and cp["duration_s"] == 6 * H
    assert cp["length"] == 3 and cp["slack_s"] == cp["wall_s"] - 6 * H


def test_rework_counts_failed_gate_and_late_bug_once():
    log = twelve()  # every item has a failed then passed rubber_duck
    log.add("bug.found", "x", "a1", T0 + 30 * H, item="T01", summary="late")
    got = _run(log)
    assert got["rework"]["returned_from_gate"] == 12
    assert got["rework"]["reworked"] == 12  # the bug item is already counted
    assert got["rework"]["rate"] == 1.0
    log2 = Log()
    for n in range(10):
        tid = f"Q{n}"
        log2.task(tid, f"{tid}.py")
        log2.claim("a1", tid, T0 + n * H, f"{tid}.py")
        log2.complete("a1", tid, T0 + n * H + 600)
    log2.add("bug.found", "b1", "a1", T0 + 20 * H, item="Q0", summary="s")
    log2.add("bug.found", "b2", "a1", T0 + 20 * H + 8 * 24 * H, item="Q1", summary="s")
    r = _run(log2)["rework"]
    assert (r["bug_after_merge"], r["returned_from_gate"], r["reworked"]) == (1, 0, 1)


def test_thin_window_is_none_not_zero():
    got = _run(twelve(), since=T0 + 1000 * H)
    assert got["merged"] == 0
    assert got["throughput"]["per_hour"] is None and got["rework"]["rate"] is None
    assert got["cost"]["agent_hours_per_item"] is None


def test_cost_proxy_and_usd_when_recorded():
    log = twelve()
    base = _run(log)
    assert base["cost"]["usd_per_item"] is None and "cost_usd" in base["unavailable"]
    assert base["cost"]["agent_hours_per_item"] > 0
    for n in range(1, 13):
        log.add("session.note", f"T{n:02d}", "a1", T0 + n, cost_usd=0.5)
    got = _run(log)["cost"]
    assert got["usd_total"] == 6.0 and got["usd_per_item"] == 0.5


def test_until_cuts_the_window():
    got = _run(twelve(), until=T0 + 30 * H)
    assert 0 < got["merged"] < 12


def test_compare_against_targets():
    before = {
        "throughput": {"per_hour": 1.0},
        "cost": {"usd_per_item": None, "agent_hours_per_item": 2.0},
        "rework": {"rate": 0.10},
    }
    after = {
        "throughput": {"per_hour": 2.0},
        "cost": {"usd_per_item": None, "agent_hours_per_item": 2.4},
        "rework": {"rate": 0.14},
    }
    c = S.compare(before, after)
    assert c["throughput_x"] == 2.0 and c["cost_basis"] == "agent_hours"
    assert abs(c["cost_x"] - 1.2) < 1e-9 and abs(c["rework_points"] - 4.0) < 1e-9
    assert c["targets"] == {"throughput": True, "cost": True, "rework": True}
    after["rework"]["rate"] = 0.20
    assert S.compare(before, after)["targets"]["rework"] is False
    assert (
        S.compare(before, {**after, "throughput": {"per_hour": None}})["targets"]["throughput"]
        is None
    )


def test_provider_registers_with_the_shared_service():
    M.register("swarm-test", S.provider, replace=True)
    try:
        log = twelve()
        st = fold(list(log.events), strict=False)
        ans = M.run(log.events, st, Config(), NOW, view="summary")
        rows = {r["metric"]: r for r in ans.metrics["providers"]}
        assert rows["swarm.merged_per_hour"]["value"] > 0
    finally:
        M.unregister("swarm-test")


def test_provider_rows_withheld_under_min_samples():
    log = Log()
    log.task("A", "a.py")
    log.claim("a1", "A", T0, "a.py")
    log.complete("a1", "A", T0 + H)
    M.register("swarm-thin", S.provider, replace=True)
    try:
        st = fold(list(log.events), strict=False)
        rows = M.run(log.events, st, Config(), NOW, view="summary").metrics["providers"]
        mine = [r for r in rows if r["metric"].startswith("swarm.")]
        assert mine and all(r["value"] is None and "samples" in r["unavailable"] for r in mine)
    finally:
        M.unregister("swarm-thin")

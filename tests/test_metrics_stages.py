"""`ddflow metrics stages`: the cycle-time split, on a seeded log with known durations."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import run_cli
from helpers import rpc_call, tool_json
from lanes_fixture import T0, Log
from metrics_fixture import frozen

from ddflow.services import metrics as M
from ddflow.services.metrics import facts as F
from ddflow.surfaces.mcp import Server

R = "rubber_duck"


def seeded() -> Log:
    """P1/A: waited on a lease 500 s, ready 1600 s before its claim, two review rounds, one
    gate with host-lock wait, merged. P2/B: a bug-fix kind, never merged, no gate."""
    log = Log()
    log.add("phase.added", "P1", "lead", T0 - 2000, title="p1", body="")
    log.add("phase.added", "P2", "lead", T0 - 2000, title="p2", body="")
    for tid, phase in (("A", "P1"), ("B", "P2")):
        log.add("task.added", tid, "lead", T0 - 1000, title=tid, body="", parent=phase)
    log.add("lease.waiting", "A", "a1", T0 + 100, at=T0 + 100, globs=["x.py"], holder="z", on=[])
    log.claim("a1", "A", T0 + 600, "x.py")
    log.add("gate.started", "A", "a1", T0 + 900, gate="research")
    log.add(
        "gate.passed", "A", "a1", T0 + 1000, gate="research", by="a1",
        evidence={"waited_s": 20, "diff_stat": {"insertions": 30, "deletions": 10}},
    )  # fmt: skip
    log.add(
        "gate.failed", "A", "a1", T0 + 2000, gate=R, by="r1",
        evidence={"model": "m", "elapsed_s": 300},
    )  # fmt: skip
    log.add(
        "gate.passed", "A", "a1", T0 + 3000, gate=R, by="r1",
        evidence={"model": "m", "elapsed_s": 200},
    )  # fmt: skip
    log.add("worktree.merged", "A", "a1", T0 + 3300, sha="abc", branch="b")
    log.complete("a1", "A", T0 + 3400)
    log.release("a1", "A", T0 + 3401)
    log.claim("a2", "B", T0 + 700)
    log.complete("a2", "B", T0 + 800)
    return log


def run(log: Log, **kw):
    st, cfg, now = frozen(log)
    cfg.metrics.min_samples = 1
    for k, v in kw.pop("knobs", {}).items():
        setattr(cfg.metrics, k, v)
    return M.run(log.events, st, cfg, now, view="stages", **kw)


def stages(ans, group="(all)") -> dict:
    (g,) = [g for g in ans.metrics["groups"] if g["group"] == group]
    return g["stages"]


def test_each_stage_is_cut_from_existing_events():
    s = stages(run(seeded()))
    assert s["queue_wait"]["p50_s"] == pytest.approx(1650)  # A: 1600, B: 1700 -> median
    assert s["lease_wait"]["n"] == 1 and s["lease_wait"]["p50_s"] == pytest.approx(500)
    assert s["implement"]["p50_s"] == pytest.approx(300)  # claim to first gate
    assert s["review_wait"]["p50_s"] == pytest.approx(800)  # 1300 s span less 500 s reviewing
    assert s["merge_wait"]["p50_s"] == pytest.approx(300)  # last verdict to merged
    assert s["claim_to_merge"]["p50_s"] == pytest.approx(2700)
    assert s["rework_rounds"]["n"] == 1 and s["rework_rounds"]["p50_rounds"] == 1
    assert s["gate_reruns"]["p50_runs"] == 1  # rubber_duck ran twice


def test_a_stage_with_no_samples_is_none_not_zero():
    s = stages(run(seeded()))
    assert s["claim_to_merge"]["n"] == 1  # B was never merged: no sample, not 0
    ans = run(seeded(), knobs={"min_samples": 5})
    assert all(v["p50_s"] is None for v in stages(ans).values() if "p50_s" in v)
    assert "queue_wait" in ans.metrics["unavailable"]
    assert not ans.ok and "not enough data" in ans.reason


def test_gate_time_separates_the_host_lock_wait():
    (g,) = [r for r in run(seeded()).metrics["groups"][0]["gates"] if r["gate"] == "research"]
    assert g["run"]["p50_s"] == pytest.approx(80)  # 100 s elapsed less 20 s queued
    assert g["wait"]["p50_s"] == pytest.approx(20)
    (d,) = [r for r in run(seeded()).metrics["groups"][0]["gates"] if r["gate"] == R]
    assert d["wait"]["n"] == 0 and d["wait"]["p50_s"] is None  # never recorded: not 0


def test_unmeasured_stages_are_named_never_zero():
    nm = run(seeded()).metrics["not_measured"]
    assert set(nm) == {"claim_refusals", "human_wait"}
    assert all("P-observe" in why for why in nm.values())
    assert "claim_refusals" not in stages(run(seeded()))


def test_group_by_phase_kind_agent_and_size():
    ans = run(seeded(), group_by="phase")
    assert sorted(g["group"] for g in ans.metrics["groups"]) == ["P1", "P2"]
    assert stages(ans, "P1")["merge_wait"]["n"] == 1
    assert stages(ans, "P2")["merge_wait"]["n"] == 0
    assert sorted(g["group"] for g in run(seeded(), group_by="agent").metrics["groups"]) == [
        "a1",
        "a2",
    ]
    assert [g["group"] for g in run(seeded(), group_by="kind").metrics["groups"]] == ["task"]
    size = sorted(g["group"] for g in run(seeded(), group_by="size").metrics["groups"])
    assert size == ["(unknown size)", "small (<=50 lines)"]  # A changed 40, B never measured


def test_size_buckets_are_knobs():
    ans = run(seeded(), group_by="size", knobs={"stage_size_small": 10, "stage_size_large": 30})
    assert "large (>30 lines)" in [g["group"] for g in ans.metrics["groups"]]


def test_unknown_group_by_names_the_choices():
    ans = run(seeded(), group_by="colour")
    assert ans.failed and "phase" in ans.reason and "size" in ans.reason


def test_answer_names_the_next_call_in_both_forms():
    nxt = run(seeded()).metrics["next"]
    assert nxt["cli"].startswith("ddflow metrics retro ")
    assert "ddflow_metrics" in nxt["mcp"] and "view=retro" in nxt["mcp"]


def test_facts_carry_waits_merges_and_changed_lines():
    st, _cfg, now = frozen(seeded())
    facts = F.collect(seeded().events, st, now)
    assert facts.waits["A"] == [T0 + 100]
    assert facts.merges["A"] == [T0 + 3300]
    assert max(o.changed_lines or 0 for o in facts.outcomes if o.item == "A") == 40


def test_cli_and_mcp_return_the_stages_view(repo):
    run_cli(repo, "init")
    code, out, _ = run_cli(repo, "metrics", "stages", "--json", agent="reader")
    assert code == 2, out  # a bare project holds too little: exit 2, never a zero
    cli = json.loads(out)
    mcp = tool_json(rpc_call(Server(repo), "ddflow_metrics", view="stages"))
    assert cli["view"] == mcp["view"] == "stages"
    assert set(cli) == set(mcp)

"""The fleet registry: a fold over the lane ledger plus the stop reasons the log records."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lanes_fixture import T0, Log, build_inputs, multi_lane

from ddflow.services import fleet as FL
from ddflow.services import lanes as LN

NOW = T0 + 1901


def _entries(g: Log, now: float = NOW, cfg_edit=None):
    st, cfg, plan = build_inputs(g.events, now)
    if cfg_edit:
        cfg_edit(cfg)
    ledger = FL.ledger(g.events, st, cfg, now, plan)
    return FL.registry(g.events, ledger, st, cfg, now), ledger, st, cfg


def test_classes_of_a_recorded_log():
    entries, *_ = _entries(multi_lane())
    got = {e.name: e.cls for e in entries}
    assert got == {
        "lane-ended": FL.STALLED,
        "lane-gone": FL.STALLED,
        "lane-idle": FL.IDLE,
        "lane-stale": FL.STALLED,
        "lane-work": FL.WORKING,
    }


def test_a_rate_limit_stop_reason_makes_the_lane_failed():
    g = multi_lane()
    g.add("session.ended", "s2", "lane-gone", T0 - 150, summary="HTTP 429 rate limit hit")
    entries, *_ = _entries(g)
    e = next(x for x in entries if x.name == "lane-gone")
    assert e.cls == FL.FAILED and "429" in e.stop_reason


def test_lease_expired_reason_is_the_stop_reason():
    g = multi_lane()
    g.add("lease.expired", "U", "sweeper", T0 + 1000, holder="lane-gone", reason="spend limit")
    entries, *_ = _entries(g)
    e = next(x for x in entries if x.name == "lane-gone")
    assert e.stop_reason == "spend limit" and e.cls == FL.FAILED


def test_failure_patterns_are_a_knob():
    g = multi_lane()
    g.add("session.ended", "s2", "lane-gone", T0 - 150, summary="weird mood")

    def edit(cfg):
        cfg.lanes.fleet_failed_patterns = ["weird"]

    entries, *_ = _entries(g, cfg_edit=edit)
    assert next(x for x in entries if x.name == "lane-gone").cls == FL.FAILED


def test_finished_item_is_done():
    g = Log()
    g.task("X", "f.py")
    g.session("a", "s1", T0, ended=T0 + 5, tool="codex", model="m")
    g.claim("a", "X", T0, "f.py")
    g.complete("a", "X", T0 + 4)
    entries, *_ = _entries(g, T0 + 10)
    assert entries[0].cls == FL.DONE and entries[0].harness == "codex"


def test_registry_keeps_agents_older_than_the_lane_window():
    g = multi_lane()
    entries, *_ = _entries(g, T0 + 10 * 86400)
    st, cfg, plan = build_inputs(g.events, T0 + 10 * 86400)
    assert not LN.build(g.events, st, cfg, T0 + 10 * 86400, plan)  # the ledger forgot them
    assert {e.name for e in entries} >= {"lane-work", "lane-gone"}


def test_resume_is_idempotent_and_pure():
    g = multi_lane()
    entries, _l, st, _c = _entries(g)
    a = FL.resume(entries, "lane-gone", st, NOW)
    b = FL.resume(entries, "lane-gone", st, NOW)
    assert a == b and a["eligible"] and a["command"] == "ddflow lanes brief lane-gone"


def test_resume_refuses_working_and_finished_lanes():
    entries, _l, st, _c = _entries(multi_lane())
    assert not FL.resume(entries, "lane-work", st, NOW)["eligible"]
    assert not FL.resume(entries, "nobody", st, NOW)["eligible"]


def test_resume_already_taken_over_names_the_new_holder():
    g = multi_lane()
    g.claim("lane-new", "U", T0 + 1800, "u.py")  # a new lane picks up U (lane-gone's item)
    entries, _l, st, _c = _entries(g, T0 + 1850)
    r = FL.resume(entries, "lane-gone", st, T0 + 1850)
    assert not r["eligible"] and "lane-new" in r["reason"]


def test_registry_is_ordered_by_urgency_then_name():
    entries, *_ = _entries(multi_lane())
    assert [(e.cls, e.name) for e in entries] == [
        (FL.STALLED, "lane-ended"),
        (FL.STALLED, "lane-gone"),
        (FL.STALLED, "lane-stale"),
        (FL.IDLE, "lane-idle"),
        (FL.WORKING, "lane-work"),
    ]


def test_stop_reason_is_cut_and_made_safe():
    g = multi_lane()
    g.add("session.ended", "s2", "lane-gone", T0 - 150, summary="x`y" * 1000)
    entries, *_ = _entries(g, cfg_edit=lambda c: setattr(c.lanes, "fleet_reason_chars", 50))
    r = next(x for x in entries if x.name == "lane-gone").stop_reason
    assert len(r) <= 50 and "`" not in r


def test_fleet_and_resume_over_cli_and_mcp(repo):
    import json

    from conftest import run_cli
    from helpers import rpc_call, tool_json

    from ddflow.surfaces.mcp import Server

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "X", "--title", "X fixture", "--globs", "f.py")
    assert run_cli(repo, "claim", "X", "--no-worktree", agent="holder")[0] == 0
    code, out, _ = run_cli(repo, "lanes", "fleet", "--json")
    cli = json.loads(out)
    mcp = tool_json(rpc_call(Server(repo), "ddflow_lanes", view="fleet"))
    assert code == 0 and [(e["name"], e["cls"]) for e in cli["fleet"]] == [("holder", "working")]
    strip = lambda rows: [{k: v for k, v in r.items() if k != "heartbeat_age_s"} for r in rows]  # noqa: E731
    assert strip(cli["fleet"]) == strip(mcp["fleet"])
    code, out, _ = run_cli(repo, "lanes", "resume", "holder", "--json")
    assert code == 2 and json.loads(out)["resume"]["eligible"] is False
    assert run_cli(repo, "lanes", "resume", "--json")[0] == 1


def test_resume_ignores_a_lapsed_lease_of_another_agent():
    g = multi_lane()
    g.claim("lane-other", "U", T0 - 250, "u.py")  # a lease that lapsed long before NOW
    entries, _l, st, cfg = _entries(g, T0 + 5000)
    r = FL.resume(entries, "lane-gone", st, T0 + 5000, cfg.lease.grace_s)
    assert "taken over" not in r["reason"]

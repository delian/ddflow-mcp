"""The fleet registry: a fold over the lane ledger plus the stop reasons the log records."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest
from lanes_fixture import T0, Log, build_inputs, multi_lane

from ddflow.api import fleet as AF
from ddflow.api import lane_views as LV
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


def test_registry_puts_lost_and_live_work_before_idle_newest_first():
    entries, *_ = _entries(multi_lane())
    assert [(e.cls, e.name) for e in entries] == [
        (FL.STALLED, "lane-stale"),
        (FL.STALLED, "lane-ended"),
        (FL.STALLED, "lane-gone"),
        (FL.WORKING, "lane-work"),
        (FL.IDLE, "lane-idle"),
    ]


def test_stop_reason_is_cut_and_made_safe():
    g = multi_lane()
    g.add("session.ended", "s2", "lane-gone", T0 - 150, summary="x`y" * 1000)
    entries, *_ = _entries(g, cfg_edit=lambda c: setattr(c.lanes, "fleet_reason_chars", 50))
    r = next(x for x in entries if x.name == "lane-gone").stop_reason
    assert 0 < len(r) <= 50 and "`" not in r and r.startswith("x y")


def test_the_display_cut_never_changes_the_class():
    g = multi_lane()
    g.add("lease.expired", "U", "sweeper", T0 + 1000, holder="lane-gone", reason="HTTP 429")

    def edit(cfg):
        cfg.lanes.fleet_reason_chars = 0

    e = next(x for x in _entries(g, cfg_edit=edit)[0] if x.name == "lane-gone")
    assert e.cls == FL.FAILED and e.stop_reason == ""


def test_a_failure_marker_past_the_raw_cap_is_still_matched():
    """Patterns match the whole recorded reason, so a marker past 2000 chars classes failed."""
    g = multi_lane()
    reason = ("x" * 2100) + " quota exceeded"
    g.add("lease.expired", "U", "sweeper", T0 + 1000, holder="lane-gone", reason=reason)
    entries, *_ = _entries(g)
    e = next(x for x in entries if x.name == "lane-gone")
    assert e.cls == FL.FAILED


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
    ticking = ("heartbeat_age_s", "lease_s")  # both count from the moment of the call
    strip = lambda rows: [{k: v for k, v in r.items() if k not in ticking} for r in rows]  # noqa: E731
    assert strip(cli["fleet"]) == strip(mcp["fleet"])
    family = tool_json(rpc_call(Server(repo), "ddflow_view", action="lanes", view="fleet"))
    assert strip(family["fleet"]) == strip(cli["fleet"])
    code, out, _ = run_cli(repo, "lanes", "resume", "holder", "--json")
    body = json.loads(out)
    assert code == 2 and body["resume"]["eligible"] is False
    assert body["fleet"] == [] and body["lanes"] == []  # one answer, not two listings
    assert run_cli(repo, "lanes", "resume", "--json")[0] == 1


def test_resume_ignores_a_lapsed_lease_of_another_agent():
    g = multi_lane()
    g.claim("lane-other", "U", T0 - 250, "u.py")  # a lease that lapsed long before NOW
    entries, _l, st, cfg = _entries(g, T0 + 5000)
    r = FL.resume(entries, "lane-gone", st, T0 + 5000, cfg.lease.grace_s)
    assert "taken over" not in r["reason"]


def test_a_finished_lane_keeps_no_old_failure_reason():
    g = Log()
    g.task("X", "f.py")
    g.session("a", "s1", T0, ended=T0 + 4)
    g.claim("a", "X", T0, "f.py")
    g.add("session.ended", "s0", "a", T0 - 5, summary="HTTP 429")
    g.complete("a", "X", T0 + 3)
    e = _entries(g, T0 + 10)[0][0]
    assert e.cls == FL.DONE and e.stop_reason == ""


@pytest.mark.parametrize("note", ["completed", "merged", "done"])
def test_a_normal_release_clears_an_older_stop_reason(note):
    g = multi_lane()
    g.add("lease.expired", "V", "sweeper", T0 - 90, holder="lane-ended", reason="HTTP 429")
    g.add("lease.released", "V", "lane-ended", T0 - 80, holder="lane-ended", note=note)
    e = next(x for x in _entries(g)[0] if x.name == "lane-ended")
    assert e.lane_state in FL.S.UNFINISHED  # still stopped: only the reason is stale
    assert (e.cls, e.stop_reason, e.stop_at) == (FL.STALLED, "", 0.0)


def test_coming_back_clears_an_older_stop_reason():
    g = multi_lane()  # lane-gone claimed at T0 - 300 and then lapsed, saying nothing
    g.add("session.ended", "s9", "lane-gone", T0 - 350, summary="HTTP 429")
    e = next(x for x in _entries(g)[0] if x.name == "lane-gone")
    assert (e.cls, e.stop_reason) == (FL.STALLED, "")


def test_a_stop_after_coming_back_is_the_reason():
    g = multi_lane()
    g.add("session.ended", "s9", "lane-gone", T0 - 350, summary="weird mood")
    g.add("lease.expired", "U", "sweeper", T0 - 100, holder="lane-gone", reason="spend limit")
    e = next(x for x in _entries(g)[0] if x.name == "lane-gone")
    assert (e.cls, e.stop_reason) == (FL.FAILED, "spend limit")


def test_an_entry_carries_role_items_and_lease():
    g = Log()
    for tid in ("A", "B", "C"):
        g.task(tid, f"{tid}.py")
    g.session("a", "s1", T0, role="reviewer")
    g.claim("a", "A", T0, "A.py")
    g.release("a", "A", T0 + 1)
    g.claim("a", "B", T0 + 2, "B.py")
    g.release("a", "B", T0 + 3)
    g.claim("a", "A", T0 + 4, "A.py")
    g.release("a", "A", T0 + 5)
    g.claim("a", "C", T0 + 6, "C.py")
    e = _entries(g, T0 + 10)[0][0]
    assert (e.role, e.items, e.items_total) == ("reviewer", ("C", "A", "B"), 3)
    assert e.lease_s > 0
    assert _entries(g, T0 + 10, lambda c: setattr(c.lanes, "fleet_items", 2))[0][0].items == (
        "C",
        "A",
    )
    assert _entries(g, T0 + 10 * 86400)[0][0].lease_s == 0  # lapsed: no lease to show


def _many(n: int) -> Log:
    g = Log()
    g.task("X", "f.py")
    for i in range(n):
        g.session(f"lane-{i:03}", f"s{i}", T0 + i, tool="codex", model="m" * 40)
    return g


def _view(g: Log, *, rows: int | None = None):
    st, cfg, plan = build_inputs(g.events, T0 + 1000)
    if rows is not None:
        cfg.lanes.fleet_max_rows = rows
    data = {"fleet": [], "fleet_total": 0, "lanes": [], "resume": {}, "text": ""}
    return AF.fleet_view(
        data, view="fleet", lane="", events=g.events, st=st, cfg=cfg, now=T0 + 1000, plan=plan
    )


def test_the_listing_is_capped_with_a_visible_stop():
    out = _view(_many(12), rows=5)
    assert out.data["fleet_total"] == 12 and len(out.data["fleet"]) == 5
    assert "7 more" in out.data["text"] and "lanes.fleet_max_rows" in out.data["text"]
    assert out.data["fleet"][0]["name"] == "lane-011"  # the newest stays


def test_a_cap_of_zero_lists_everyone():
    out = _view(_many(12), rows=0)
    assert len(out.data["fleet"]) == 12 and "more" not in out.data["text"]


def test_the_default_result_stays_under_the_result_ceiling_at_its_worst():
    """Every row as big as a real one gets: stopped, a long reason, a full list of long ids."""
    import json

    g = Log()
    for i in range(40):
        ids = [f"fix-B-a-long-enough-id-{i:03}-{n}" for n in range(6)]
        for tid in ids:
            g.task(tid, f"{tid}.py")
        name = f"Monster3-bridge-cse_01{i:03}-a-long-agent-identity"
        g.session(name, f"s{i}", T0, tool="claude-code", model="claude-long-model-name-20261105")
        for n, tid in enumerate(ids):
            g.claim(name, tid, T0 + n, f"{tid}.py")
        g.add("lease.expired", ids[-1], name, T0 + 10, holder=name, reason="swept: " + "x" * 400)
    out = _view(g)
    assert out.data["fleet_total"] == 40 and out.data["fleet"][0]["stop_reason"]
    assert len(json.dumps(out.data)) < 20_000


def test_the_brief_resume_offers_reaches_an_agent_past_the_lane_window(repo):
    from conftest import run_cli

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "X", "--title", "X fixture", "--globs", "f.py")
    assert run_cli(repo, "claim", "X", "--no-worktree", agent="holder")[0] == 0
    later = time.time() + 30 * 86400  # past [lanes] window_s, and the lease has lapsed
    got = LV.lanes(repo, view="resume", lane="holder", now=later).data["resume"]
    assert got["eligible"] and got["command"] == "ddflow lanes brief holder"
    assert "holder" not in [x["name"] for x in LV.lanes(repo, now=later).data["lanes"]]
    assert LV.lanes(repo, view="brief", lane="holder", now=later).data["lane"] == "holder"


def test_a_brief_for_nobody_still_names_only_the_active_lanes(repo):
    from conftest import run_cli

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "X", "--title", "X fixture", "--globs", "f.py")
    assert run_cli(repo, "claim", "X", "--no-worktree", agent="holder")[0] == 0
    run_cli(repo, "release", "X", agent="holder")
    later = time.time() + 30 * 86400
    out = LV.lanes(repo, view="brief", lane="nobody", now=later)
    assert out.exit != 0 and "none active" in out.reason and "holder" not in out.reason


def test_the_all_history_ledger_pays_for_no_standing_waits(monkeypatch):
    """Waits cost agents x waits x leases, and the registry shows none: 119 of 141 s measured."""
    from ddflow.services import waiting as WG

    g = multi_lane()
    st, cfg, plan = build_inputs(g.events, NOW)
    wait = {"holder": "lane-idle", "globs": ["f.py"], "since": NOW, "queue": True, "at": NOW}
    st.waiting = {"W": {**wait, "lease_event": "", "ttl_s": 5400}}
    assert WG.standing(st, cfg, NOW)  # a live wait, so an unfixed ledger does the work
    calls = []
    monkeypatch.setattr(WG, "edges", lambda *a: calls.append(a) or [])
    lanes = FL.ledger(g.events, st, cfg, NOW, plan)
    assert len(lanes) == 5 and calls == [] and "W" in st.waiting  # the caller's state is intact


@pytest.mark.parametrize("note", ["", "completed"])
def test_a_release_by_another_agent_keeps_the_stop_reason(note):
    """Someone sweeping the lease says nothing about the holder: 87 such releases on record."""
    g = multi_lane()
    g.add("lease.expired", "V", "sweeper", T0 - 90, holder="lane-ended", reason="HTTP 429")
    g.add("lease.released", "V", "sweeper", T0 - 80, holder="lane-ended", by="sweeper", note=note)
    e = next(x for x in _entries(g)[0] if x.name == "lane-ended")
    assert e.cls == FL.FAILED and "429" in e.stop_reason


def test_a_cleared_reason_leaves_the_lane_resumable():
    g = multi_lane()
    g.add("lease.expired", "V", "sweeper", T0 - 90, holder="lane-ended", reason="HTTP 429")
    g.add("lease.released", "V", "lane-ended", T0 - 80, holder="lane-ended", note="completed")
    entries, _l, st, cfg = _entries(g)
    assert FL.resume(entries, "lane-ended", st, NOW, cfg.lease.grace_s)["eligible"]


def test_a_lease_inside_its_grace_still_shows_as_held():
    g = multi_lane()
    st, *_ = build_inputs(g.events, T0)
    ls = st.items["X"].lease
    now = ls.renewed_at + ls.ttl_s + 10  # past the ttl, inside the grace below

    def edit(cfg):
        cfg.lease.grace_s = 30

    e = next(x for x in _entries(g, now, edit)[0] if x.name == "lane-work")
    assert e.lease_s == 1 and e.lane_state in FL.S.ACTIVE  # held, with the ttl spent


def test_lease_seconds_stop_at_the_ttl_not_the_grace():
    g = multi_lane()
    st, *_ = build_inputs(g.events, T0)
    ls = st.items["X"].lease
    e = next(
        x
        for x in _entries(g, ls.renewed_at, lambda c: setattr(c.lease, "grace_s", 30))[0]
        if x.name == "lane-work"
    )
    assert e.lease_s == ls.ttl_s


def test_an_empty_release_says_nothing_about_a_stop():
    g = multi_lane()
    g.add("lease.expired", "V", "sweeper", T0 - 90, holder="lane-ended", reason="HTTP 429")
    g.add("lease.released", "V", "lane-ended", T0 - 80, holder="lane-ended", note="")
    e = next(x for x in _entries(g)[0] if x.name == "lane-ended")
    assert e.cls == FL.FAILED and "429" in e.stop_reason


def _held(by: str, *, lapsed: bool = False, then: str = ""):
    g = Log()
    g.task("X`y", "f.py")
    g.claim(by, "X`y", T0, "f.py")
    now = T0 + 1
    if lapsed:
        now = T0 + 100_000
    if then:
        g.claim(then, "X`y", now - 1, "f.py")
    return g, now


@pytest.mark.parametrize(
    "shape",
    [
        {"by": "a`b"},  # still holds it
        {"by": "a`b", "lapsed": True},  # eligible
        {"by": "a`b", "lapsed": True, "then": "c`d"},  # taken over
    ],
)
def test_a_resume_answer_never_prints_a_name_raw(shape):
    g, now = _held(**shape)
    entries, _l, st, cfg = _entries(g, now)
    r = FL.resume(entries, "a`b", st, now, cfg.lease.grace_s)
    assert r["reason"] and "`" not in r["reason"]

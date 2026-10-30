"""Lane conformance levels: pure grading, a live run against stub agents, and the stored level."""

from __future__ import annotations

import json
import sys
import textwrap

import pytest

from ddflow.config import Config
from ddflow.services import lane_driver as ld
from ddflow.services import lane_headless as lh
from ddflow.services.engines import stream_map
from ddflow.services.swarm import conformance as cf
from ddflow.services.swarm import conformance_io as cio

TOOLS = frozenset(Config().swarm.conformance_tools)
P = cf.Params(min_accuracy=0.9, min_calls=5, idle_s=0.4, lease_wait_s=1.5, tools=TOOLS)
RULES = stream_map.parse_rules(
    [
        {"when": {"path": "type", "equals": "session"}, "emit": "started", "fields": {}},
        {
            "when": {"path": "type", "equals": "call"},
            "emit": "tool",
            "fields": {"name": "tool", "action": "action", "ok": "ok"},
        },
    ]
)
# A stub agent: prints a recorded script of events; with --loop it keeps calling a tool until
# the revocation file appears (obey) or for ever (ignore).
AGENT = textwrap.dedent(
    """
    import json, os, sys, time
    script, mode, flag = json.load(open(sys.argv[1])), sys.argv[2], sys.argv[3]
    for line in script:
        print(json.dumps(line), flush=True)
    if mode != "once":
        end = time.time() + 20
        while time.time() < end:
            if mode == "obey" and os.path.exists(flag):
                sys.exit(0)
            print(json.dumps({"type": "call", "tool": "show"}), flush=True)
            time.sleep(0.05)
    """
)


def call(tool, **kw):
    return {"type": "call", "tool": tool, **kw}


GOOD = [
    {"type": "session"},
    call("mcp__ddflow__ddflow_claim"),
    call("ddflow_heartbeat"),
    call("ddflow_gate", action="record"),
    call("show"),
    call("brief"),
]


def lane(tmp_path, script, mode="once"):
    agent = tmp_path / "agent.py"
    agent.write_text(AGENT)
    flag = tmp_path / "revoked"
    rec = tmp_path / "script.json"
    rec.write_text(json.dumps(script))
    spec = ld.LaunchSpec(
        name="stub",
        argv=(sys.executable, str(agent), str(rec), mode, str(flag)),
        stream_rules=RULES,
        stop_grace_s=1,
        pass_env=("PATH",),
    )
    return spec, flag


def level(spec, **kw):
    return cf.run("stub", lh.ProcessHeadless(), spec, P, **kw)


def statuses(r):
    return {o.check: o.status for o in r.outcomes}


def test_ladder_climbs_to_the_probe_that_was_not_given(tmp_path):
    spec, _ = lane(tmp_path, GOOD)
    r = level(spec)
    assert r.level == 4 and r.level_name == "gate_record"
    s = statuses(r)
    assert s["hook"] == cf.UNAVAILABLE and s["lease_stop"] == cf.UNAVAILABLE
    assert s["tool_accuracy"] == cf.PASSED  # judged alone, but never lifts the level


def test_full_run_passes_every_level(tmp_path):
    spec, flag = lane(tmp_path, GOOD, mode="obey")
    r = level(spec, hook_probe=lambda: True, revoke=lambda: flag.write_text("x"))
    assert r.level == 7 and r.level_name == "tool_accuracy", r.outcomes
    assert all(o.status == cf.PASSED for o in r.outcomes)


def test_lane_that_ignores_the_lease_fails_lease_stop(tmp_path):
    spec, flag = lane(tmp_path, GOOD, mode="ignore")
    r = level(spec, hook_probe=lambda: True, revoke=lambda: flag.write_text("x"))
    assert r.level == 5 and statuses(r)["lease_stop"] == cf.FAILED


def test_failed_hook_stops_the_climb(tmp_path):
    spec, flag = lane(tmp_path, GOOD, mode="obey")
    r = level(spec, hook_probe=lambda: False, revoke=lambda: flag.write_text("x"))
    assert r.level == 4 and statuses(r)["hook"] == cf.FAILED


def test_hook_probe_error_is_unavailable_not_passed(tmp_path):
    spec, _ = lane(tmp_path, GOOD)

    def boom():
        raise RuntimeError("nope")

    r = level(spec, hook_probe=boom)
    assert statuses(r)["hook"] == cf.UNAVAILABLE and r.level == 4


def test_lane_that_never_claims_stays_below_claim(tmp_path):
    spec, _ = lane(tmp_path, [{"type": "session"}, call("show")])
    r = level(spec)
    assert r.level == 1 and statuses(r)["claim"] == cf.FAILED


def test_failed_claim_call_is_not_a_claim(tmp_path):
    spec, _ = lane(tmp_path, [{"type": "session"}, call("claim", ok=False)])
    assert level(spec).level == 1


def test_no_started_event_fails_launch(tmp_path):
    spec, _ = lane(tmp_path, [call("claim")])
    r = level(spec)
    assert r.level == 0 and statuses(r)["launch"] == cf.FAILED


def test_unlaunchable_lane_is_unavailable(tmp_path):
    r = level(ld.LaunchSpec(name="x", argv=("{prompt}",)))
    assert r.level == 0 and statuses(r)["launch"] == cf.FAILED  # the driver refused it
    r = cf.grade("x", cf.Observed(start_error="unavailable: no transport"), P)
    assert statuses(r)["launch"] == cf.UNAVAILABLE


def test_no_tool_event_capability_is_unavailable():
    o = cf.Observed(started=True, stopped_clean=True, tool_events=False)
    r = cf.grade("x", o, P)
    assert r.level == 1 and statuses(r)["claim"] == cf.UNAVAILABLE
    assert statuses(r)["tool_accuracy"] == cf.UNAVAILABLE


def test_lane_that_ended_before_revocation_proves_nothing(tmp_path):
    spec, flag = lane(tmp_path, GOOD, mode="once")
    r = level(spec, hook_probe=lambda: True, revoke=lambda: flag.write_text("x"))
    assert statuses(r)["lease_stop"] == cf.UNAVAILABLE and r.level == 5


def grade_calls(*calls):
    o = cf.Observed(started=True, stopped_clean=True, tool_events=True, hook=True, lease_stop=True)
    o.calls = [{"name": c} if isinstance(c, str) else c for c in calls]
    return cf.grade("x", o, P)


LEAD = ["claim", "heartbeat", {"name": "gate", "action": "record"}]


def test_accuracy_threshold_and_minimum_calls():
    base = [*LEAD, "brief", "show"]
    assert statuses(grade_calls(*base))["tool_accuracy"] == cf.PASSED
    assert grade_calls(*base).level == 7
    assert statuses(grade_calls(*base[:4]))["tool_accuracy"] == cf.UNAVAILABLE
    assert statuses(grade_calls())["tool_accuracy"] == cf.UNAVAILABLE
    bad = grade_calls(*base, "bash", "bash")  # 5 of 7
    assert statuses(bad)["tool_accuracy"] == cf.FAILED and bad.level == 6
    assert grade_calls(*LEAD, "bash", "bash").level == 6


def test_gate_call_for_another_action_is_not_a_record():
    listing = {"name": "gate", "action": "list"}
    r = grade_calls("claim", "heartbeat", listing)
    assert statuses(r)["gate_record"] == cf.FAILED and r.level == 3
    assert grade_calls("claim", "heartbeat", {"name": "gate", "action": "record"}).level == 6


def test_unconfirmed_stop_is_not_a_launch_pass():
    r = cf.grade("x", cf.Observed(started=True), P)
    assert r.level == 0 and statuses(r)["launch"] == cf.UNAVAILABLE
    r = cf.grade("x", cf.Observed(started=True, stopped_clean=False), P)
    assert statuses(r)["launch"] == cf.FAILED


def test_tool_name_prefixes_are_ignored():
    for n in ("mcp__ddflow__ddflow_claim", "ddflow_claim", "CLAIM", "ddflow.ddflow_claim"):
        assert cf.norm_tool(n) == "claim"


def test_report_round_trips_and_admits_by_level():
    r = cf.grade("x", cf.Observed(started=True, stopped_clean=True), P, now=100.0)
    assert cf.Report.from_data(json.loads(json.dumps(r.to_data()))) == r
    assert cf.admits(r, 1, P, now=101.0) and cf.admits(r, "launch", P, now=101.0)
    assert not cf.admits(r, "claim", P, now=101.0)
    assert not cf.admits(None, 0, P) and not cf.admits(r, "hook_fire", P, now=101.0)
    old = cf.Params(max_age_s=10, tools=TOOLS)
    assert not cf.admits(r, 1, old, now=200.0) and cf.admits(r, 1, old, now=105.0)


def test_store_replaces_a_lane_and_survives_garbage(tmp_path):
    assert cio.load_all(tmp_path) == {}
    a = cf.Report("a", 2, (cf.Outcome("launch", cf.PASSED),), 1.0)
    cio.save(tmp_path, a)
    cio.save(tmp_path, cf.Report("b", 1, (), 2.0))
    cio.save(tmp_path, cf.Report("a", 3, (), 3.0))
    assert cio.load(tmp_path, "a").level == 3 and cio.load(tmp_path, "b").level == 1
    cio.path_for(tmp_path).write_text('{"schema": "lane-levels@2", "lanes": {}}')
    assert cio.load(tmp_path, "a") is None
    with pytest.raises(ValueError):
        cio.save(tmp_path, a)  # a newer file is not clobbered
    assert "@2" in cio.path_for(tmp_path).read_text()
    cio.path_for(tmp_path).write_text("{not json")
    assert cio.load(tmp_path, "a") is None


def test_concurrent_saves_keep_every_lane(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(8) as ex:
        list(ex.map(lambda i: cio.save(tmp_path, cf.Report(f"l{i}", 1, (), 1.0)), range(16)))
    assert len(cio.load_all(tmp_path)) == 16


def test_params_come_from_config():
    p = cf.Params.from_config(Config())
    assert p.min_accuracy == 0.9 and "claim" in p.tools and p.max_events > 0


@pytest.mark.parametrize("bad", [-0.1, 1.5])
def test_accuracy_knob_is_validated(bad):
    from ddflow.config_sections.swarm import _share

    assert _share(bad)

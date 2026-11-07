"""A background orchestrator lane must keep its own lane alive across waits, so its
claim stays heartbeat-held rather than lapsing to INTERRUPTED.

Regression for Bbc611c0165. The failure mode: a ddflow-orchestrator background lane
(spawned via the harness `task` tool) self-schedules a resume via `schedule_wakeup`, then
returns a final result (turn "completed"). The harness drops a subagent's scheduled wakeup
at turn completion, so the lane never resumes: its claim lease is never renewed again and
lapses to INTERRUPTED, re-interrupting the item. The lane driver assumed a self-resume
survives turn end; it does not.

The fix keeps the lane alive instead of relying on that wakeup, at both levels ddflow
owns:

* as DATA -- a loop primitive whose lane can COMPLETE (``background-subagents``,
  ``subtask``) MUST name an ``orchestration.keepalive`` (how the lane keeps its own
  session alive across waits). A self-keeping loop (``self-paced-command``,
  ``second-window``) is exempt: the loop primitive IS its own keepalive.
* as the EMITTED INSTRUCTION -- the orchestrator template tells the lane to hold its own
  session goal open and heartbeat through every wait, never to return a final result
  while an item is in flight, and never to leave a self-scheduled wakeup behind.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from lanes_fixture import T0, build_inputs, multi_lane
from ddflow.services import fleet as FL
from ddflow.services import lanes as LN
from ddflow.services import harnessreg
from ddflow.services.lanes import states as S
from ddflow.services.harnessreg import DescriptorError, parse

BASE = """
id = "acme"
rank = 1
family = "acme"
delta = "acme.md"
[mcp]
path = ".acme/mcp.json"
shape = "mcpServers"
[verified]
date = "2026-10-29"
sources = ["https://example.com"]
"""

ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR = ROOT / "ddflow" / "templates" / "agents" / "kilo" / "ddflow-orchestrator.md"


def p(extra: str = ""):
    return parse(BASE + extra, "acme")


class TestCompletableLoopNeedsKeepalive:
    @pytest.mark.parametrize("loop", ["background-subagents", "subtask"])
    def test_a_completable_loop_without_a_keepalive_is_refused(self, loop):
        with pytest.raises(DescriptorError, match="keepalive"):
            p(f'[orchestration]\nloop = "{loop}"\nwait = "background + ddflow wait"\n')

    def test_a_completable_loop_names_how_it_stays_alive(self):
        h = p(
            """
[orchestration]
loop = "background-subagents"
keepalive = "session-goal"
wait = "background process + ddflow wait"
"""
        )
        assert h.orchestration.keepalive == "session-goal"

    @pytest.mark.parametrize("loop", ["self-paced-command", "second-window"])
    def test_a_self_keeping_loop_is_its_own_keepalive(self, loop):
        # A self-paced command / second window runs until it stops itself; it never
        # completes early, so it needs no separate keepalive named.
        h = p(f'[orchestration]\nloop = "{loop}"\nwait = "background + ddflow wait"\n')
        assert h.orchestration.keepalive == ""

    def test_a_keepalive_must_be_a_known_primitive(self):
        with pytest.raises(DescriptorError, match="keepalive"):
            p(
                """
[orchestration]
loop = "background-subagents"
keepalive = "magic"
wait = "background process + ddflow wait"
"""
            )

    def test_the_shipped_kilo_and_opencode_name_session_goal(self):
        shipped = {h.id: h for h in harnessreg.load()}
        for aid, loop in (("kilo", "background-subagents"), ("opencode", "subtask")):
            assert shipped[aid].orchestration.keepalive == "session-goal"
            assert shipped[aid].orchestration.loop == loop


class TestEmittedInstructionKeepsTheLaneAlive:
    """The emitted orchestrator instruction is the behavior: it must tell the lane to hold
    its own session open and heartbeat through every wait, never to return a final result
    while work is in flight, and never to leave a self-scheduled wakeup behind. On the
    unfixed template it said 'Do NOT wrap the mission in a session goal' and none of the
    rest, so the lane completed and its claim lapsed."""

    def test_the_orchestrator_instruction_mandates_the_keep_alive(self):
        text = ORCHESTRATOR.read_text(encoding="utf-8")
        assert "keep YOUR OWN session" in text
        assert "session goal" in text
        assert "re-loop and heartbeat" in text
        assert "Your ONE final result" in text
        # The unfixed instruction told the lane NOT to use a session goal, which is the
        # exact advice that completes the lane and drops the lobby -- it must be gone.
        assert not re.search(r"Do NOT wrap the mission in a session goal", text)


class TestHeartbeatHeldClaimIsNotInterrupted:
    """The invariant the keepalive exists to preserve: a lane whose claim is heartbeat-held
    is live (WORKING/STALE) -- never lapsed to STOPPED_* / INTERRUPTED -- and a plain resume
    offer refuses to re-interrupt it."""

    def test_a_heartbeat_held_lane_is_working(self):
        g = multi_lane()
        now = T0 + 800  # lane-work beat at T0+600: age 200 < ttl 1800 -> working
        st, cfg, plan = build_inputs(g.events, now)
        ledger = LN.build(g.events, st, cfg, now, plan)
        lane = next(l for l in ledger if l.name == "lane-work")
        assert lane.state == S.WORKING
        assert lane.state not in S.UNFINISHED

    def test_a_heartbeat_held_lane_is_not_offered_for_resume(self):
        g = multi_lane()
        now = T0 + 800
        st, cfg, plan = build_inputs(g.events, now)
        ledger = LN.build(g.events, st, cfg, now, plan)
        entries = FL.registry(g.events, ledger, st, cfg, now)
        res = FL.resume(entries, "lane-work", st, now, cfg.lease.grace_s)
        assert res["eligible"] is False
        assert "live lease" in res["reason"]

    def test_a_lapsed_lane_is_the_only_interrupted_one(self):
        g = multi_lane()
        now = T0 + 1901  # lane-gone has had no beat in >> ttl: lapsed
        st, cfg, plan = build_inputs(g.events, now)
        ledger = LN.build(g.events, st, cfg, now, plan)
        gone = next(l for l in ledger if l.name == "lane-gone")
        assert gone.state == S.STOPPED_UNKNOWN
        assert gone.state in S.UNFINISHED

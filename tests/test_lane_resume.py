"""A background orchestrator lane must keep its own lane alive across waits, so its
claim stays heartbeat-held rather than lapsing to INTERRUPTED.

Regression for Bbc611c0165. The failure mode: a ddflow-orchestrator background lane
(spawned via the harness `task` tool) self-schedules a resume via `schedule_wakeup`, then
returns a final result (turn "completed"). The harness drops a subagent's scheduled wakeup
at turn completion, so the lane never resumes: its claim lease is never renewed again and
lapses to INTERRUPTED, re-interrupting the item. The lane driver assumed a self-resume
survives turn end; it does not.

The fix models the missing fact as data: a BACKGROUND loop primitive (``background-subagents``,
``subtask``, ``second-window``) runs a lane that CAN complete, so it MUST name a ``keepalive``
-- how the lane keeps its own session alive across waits (a session goal, the second window
itself, an in-process ``ddflow wait``, a persistent process) -- instead of relying on a
wakeup it schedules for after it completes. A loop that does not complete (``self-paced-command``)
is its own keepalive.
"""

from __future__ import annotations

import pytest

from lanes_fixture import T0, build_inputs, multi_lane
from ddflow.services import fleet as FL
from ddflow.services import lanes as LN
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


def p(extra: str = ""):
    return parse(BASE + extra, "acme")


class TestBackgroundLoopNeedsKeepalive:
    def test_a_background_loop_without_a_keepalive_is_refused(self):
        with pytest.raises(DescriptorError, match="keepalive"):
            p(
                """
[orchestration]
loop = "background-subagents"
wait = "background process + ddflow wait"
"""
            )

    def test_a_background_loop_names_how_it_stays_alive(self):
        h = p(
            """
[orchestration]
loop = "background-subagents"
keepalive = "session-goal"
wait = "background process + ddflow wait"
"""
        )
        assert h.orchestration.keepalive == "session-goal"

    def test_a_self_paced_command_loop_is_its_own_keepalive(self):
        # A self-paced slash command runs until it stops itself; it never completes early,
        # so it needs no separate keepalive named.
        h = p(
            """
[orchestration]
loop = "self-paced-command"
wait = "Bash run_in_background + ScheduleWakeup"
"""
        )
        assert h.orchestration.loop == "self-paced-command"
        assert h.orchestration.keepalive == ""

    @pytest.mark.parametrize("loop", ["background-subagents", "subtask", "second-window"])
    def test_each_background_loop_needs_a_keepalive(self, loop):
        with pytest.raises(DescriptorError, match="keepalive"):
            p(f'[orchestration]\nloop = "{loop}"\nwait = "background + ddflow wait"\n')

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

"""The `Lane` port: the contract's behaviour in the default adapter and in the fake.

The same cases run against `ProcessHeadless` (a real child process replaying
tests/fixtures/lane_driver/ok.jsonl) and `FakeLane` (the same transcript, scripted), here and
generically in tests/test_ports_contract.py through `CONFORMANCE`.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from ddflow.adapters import lanes as L
from ddflow.adapters.fakes import FAKES
from ddflow.adapters.fakes.lanes import FakeLane
from ddflow.services import lane_headless as lh
from ddflow.services.engines import stream_map

FIX = Path(__file__).parent / "fixtures" / "lane_driver"
RULES = stream_map.parse_rules(
    [
        {
            "when": {"path": "type", "equals": "session"},
            "emit": "started",
            "fields": {"session": "sessionId"},
        },
        {
            "when": {"path": "type", "equals": "tool_call"},
            "emit": "tool",
            "fields": {"name": "tool"},
        },
        {
            "when": {"path": "usage.totalTokens", "exists": True},
            "emit": "usage",
            "fields": {"input": "usage.inputTokens"},
        },
        {"when": {"path": "reason.kind", "equals": "completed"}, "emit": "settled", "fields": {}},
    ]
)
#: What the recorded agent says, as the fake replays it (the same events, same order).
TRANSCRIPT = (
    ("started", {"session": "s-123"}),
    ("tool", {}),
    ("usage", {}),
    ("settled", {}),
)


def spec(**kw) -> L.LaunchSpec:
    argv = (sys.executable, str(FIX / "fake_agent.py"), str(FIX / "ok.jsonl"))
    return L.LaunchSpec(name="t", argv=argv, stream_rules=RULES, stop_grace_s=2, **kw)


def settle(lane, h) -> None:
    deadline = time.monotonic() + 10
    while lane.status(h).state == "running" and time.monotonic() < deadline:
        time.sleep(0.02)


def lane_conformance(lane) -> None:
    """Start, replay, steer, stop and refuse: what every `Lane` does the same way."""
    h = lane.start(spec())
    assert not isinstance(h, L.Result)
    assert h.id and h.transport and isinstance(h.capabilities, frozenset)
    evs = list(lane.events(h))
    assert [e.event for e in evs] == ["started", "tool", "usage", "settled"]
    assert [e.seq for e in evs] == [1, 2, 3, 4] and all(e.lane == "t" for e in evs)
    assert evs[0].fields["session"] == "s-123"
    settle(lane, h)
    st = lane.status(h)
    assert (st.state, st.exit_code, st.session) == ("exited", 0, "s-123")
    assert st.events == 4 and st.dropped == 0
    assert lane.steer(h, "go on").status == "unavailable"  # no steer capability: never imitated
    assert lane.stop(h).ok
    assert lane.status(h).state == "exited"
    assert L.advertise(lane, {"steer", "usage", "bogus"}) == {"usage"} & lane.capabilities
    refused = lane.start(L.LaunchSpec(name="x", argv=()))
    assert isinstance(refused, L.Result) and refused.status == "failed"


CONFORMANCE = {
    "Lane": {
        "real": lambda monkeypatch: lh.ProcessHeadless(),
        "fake": lambda cls: cls(TRANSCRIPT),
        "cases": lane_conformance,
    },
}


def test_the_adapter_and_the_fake_satisfy_the_port():
    assert isinstance(lh.ProcessHeadless(), L.Lane)
    assert isinstance(FakeLane(), L.Lane)
    assert FAKES["Lane"] is FakeLane


def test_the_adapter_passes_the_conformance_cases():
    lane_conformance(lh.ProcessHeadless())


def test_the_fake_replays_and_records():
    f = FakeLane(TRANSCRIPT, exit_code=75, capabilities=("steer",))
    h = f.start(spec())
    assert f.status(h).state == "running" and f.started[0].name == "t"
    assert f.steer(h, "hi").ok and h.steered == ["hi"]
    list(f.events(h))
    st = f.status(h)
    assert (st.state, st.exit_code) == ("failed", 75)


def test_the_fake_keeps_the_newest_events_when_the_buffer_is_small():
    f = FakeLane(TRANSCRIPT)
    h = f.start(spec(event_buffer=2))
    assert [e.event for e in f.events(h)] == ["usage", "settled"]
    assert f.status(h).dropped == 2


def test_the_fake_stopped_early_reports_stopped_and_can_refuse():
    f = FakeLane(TRANSCRIPT, capabilities=("steer",))
    h = f.start(spec())
    f.stop(h)
    assert f.status(h).state == "stopped"
    assert f.steer(h, "late").status == "failed" and not h.steered
    no = FakeLane(refuse=L.unavailable("no agent here"))
    assert no.start(spec()).status == "unavailable"


def test_the_contract_is_one_object_for_the_port_and_the_service_shim():
    from ddflow.services import lane_driver as ld

    for name in ("Lane", "LaunchSpec", "LaneEvent", "LaneStatus", "Result", "BUILTIN_TRANSPORTS"):
        assert getattr(ld, name) is getattr(L, name)


def test_registry_refuses_a_second_factory_and_names_the_known_ones():
    class A: ...

    with pytest.raises(ValueError):
        L.register_transport("process-headless", A)
    r = L.driver_for("no-such-transport")
    assert r.status == "unavailable" and "process-headless" in r.detail


def test_the_fake_reports_the_session_and_event_count_only_once_replayed():
    f = FakeLane(TRANSCRIPT)
    h = f.start(spec())
    assert (f.status(h).session, f.status(h).events) == ("", 0)
    list(f.events(h))
    assert (f.status(h).session, f.status(h).events) == ("s-123", 4)


def test_a_fake_stopped_before_replay_reports_no_events():
    f = FakeLane(TRANSCRIPT)
    h = f.start(spec())
    f.stop(h)
    st = f.status(h)
    assert (st.state, st.events, st.session) == ("stopped", 0, "")
    assert list(f.events(h)) == []

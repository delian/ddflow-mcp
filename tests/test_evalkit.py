"""The evaluation harness's fake kit (SE-eval-harness.2)."""

from __future__ import annotations

import pytest

import ddflow.services.evalkit as K
from ddflow.core.registry import UnknownName


def test_clock_moves_only_when_told():
    c = K.FakeClock(10)
    assert c() == 10 and c.advance(5) == 15 and c() == 15
    with pytest.raises(ValueError):
        c.advance(-1)


def test_agent_answers_in_order_records_prompts_and_raises_scripted_errors():
    a = K.FakeAgent(["one", RuntimeError("refused")])
    assert a("p1") == "one"
    with pytest.raises(RuntimeError):
        a("p2")
    assert a.prompts == ["p1", "p2"]
    with pytest.raises(K.ScriptExhausted):
        a("p3")


def test_repeat_last_answers_forever():
    t = K.FakeVendorTool([(0, "ok")], repeat_last=True)
    assert t(["x"]) == (0, "ok") and t(["y"]) == (0, "ok") and len(t.calls) == 2


def test_meters_endpoint_and_bench():
    m = K.FakeMeters()
    m.incr("a")
    m.incr("a", 2)
    m.gauge("g", 1.5)
    assert m.snapshot() == {"counters": {"a": 3}, "gauges": {"g": 1.5}}
    hub = K.FakeEndpoint(replies={"ask": "yes"}, default="?")
    assert (
        hub.send("ask", 1) == "yes"
        and hub.send("other") == "?"
        and hub.topics() == ["ask", "other"]
    )
    b = K.FakeBench({"c1": 0.9})
    assert b.run("c1") == 0.9 and b.run("zz") == 0.0 and b.runs == ["c1", "zz"]


def test_kit_builds_fresh_fakes_by_name():
    first, second = K.kit(), K.kit()
    assert set(first) == {
        "clock",
        "agent",
        "meters",
        "control_plane",
        "hub",
        "bench",
        "vendor_tool",
    }
    assert first["meters"] is not second["meters"]
    assert first["control_plane"] is not first["hub"]
    only = K.kit("clock", clock={"start": 5})
    assert list(only) == ["clock"] and only["clock"]() == 5
    with pytest.raises(UnknownName):
        K.kit("nope")


def test_agent_prompts_are_recorded_when_passed_by_keyword():
    a = K.FakeAgent(["ok"])
    assert a(prompt="do X") == "ok" and a.prompts == ["do X"]

"""The first tool call of an MCP connection carries a compact session start, once."""

from __future__ import annotations

import pytest

from ddflow.api import firstcall
from ddflow.services import harnessreg as HR
from ddflow.surfaces.mcp import Server


@pytest.fixture(autouse=True)
def _no_harness_env(monkeypatch):
    monkeypatch.setenv("DDFLOW_SESSION_FIRST_CALL", "true")
    for var in HR.session_env_vars():
        monkeypatch.delenv(var, raising=False)


def _call(srv: Server, name: str = "ddflow_status", args: dict | None = None) -> dict:
    return srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": args or {}},
        }
    )["result"]


def _texts(res: dict) -> list[str]:
    return [c["text"] for c in res["content"]]


def test_the_first_call_gets_a_second_block_and_the_second_call_does_not(repo):
    srv = Server(repo)
    srv.client_info = {"name": "some-client"}
    first = _texts(_call(srv))
    assert len(first) >= 2 and "ddflow session start" in first[1]
    assert "ddflow session start" not in "".join(_texts(_call(srv)))


def test_the_answer_stays_content_zero(repo):
    import json

    srv = Server(repo)
    srv.client_info = {"name": "some-client"}
    first = _call(srv)["content"]
    assert isinstance(json.loads(first[0]["text"]), dict)
    assert "ddflow session start" in first[1]["text"]


def test_the_brief_itself_carries_its_own_start(repo):
    srv = Server(repo)
    srv.client_info = {"name": "some-client"}
    assert "ddflow session start" not in "".join(_texts(_call(srv, "ddflow_brief")))


def test_it_is_off_when_the_knob_is_off(repo, monkeypatch):
    monkeypatch.setenv("DDFLOW_SESSION_FIRST_CALL", "false")
    srv = Server(repo)
    srv.client_info = {"name": "some-client"}
    assert "ddflow session start" not in "".join(_texts(_call(srv)))


def test_a_connection_that_never_said_who_it_is_gets_nothing(repo):
    assert "ddflow session start" not in "".join(_texts(_call(Server(repo))))


def test_it_is_clipped_to_the_token_ceiling():
    text = "x" * 10_000
    out = firstcall.clip(text, 100)
    assert len(out) < 100 * 4 + 100 and "clipped" in out


def test_a_native_hook_that_injects_the_start_turns_it_off(repo, monkeypatch):
    from ddflow.services import hookwriters as HW

    assert firstcall.hook_delivers(repo, "claude") is False
    HW.apply(repo, HR.get("claude"), "install")
    assert firstcall.hook_delivers(repo, "claude") is True
    assert firstcall.hook_delivers(repo, "aider") is False
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s")
    srv = Server(repo)
    srv.client_info = {"name": "claude-code"}
    assert "ddflow session start" not in "".join(_texts(_call(srv)))


def test_the_harness_is_found_from_the_client_name():
    assert firstcall.harness_of({"name": "gemini-cli"}, {}) == "gemini"
    assert firstcall.harness_of({"name": "claude-code"}, {}) == "claude"
    assert firstcall.harness_of({"name": "who"}, {}) == ""
    assert firstcall.harness_of({}, {"QWEN_CODE_SESSION_ID": "x"}) == "qwen"


def test_the_start_is_recorded_with_the_harness_name(repo):
    srv = Server(repo)
    srv.client_info = {"name": "qwen-code"}
    _call(srv)
    from ddflow.infra.log import EventLog

    started = [e for e in EventLog(repo, "x").read_all() if e.kind == "session.started"]
    assert started and started[-1].data["tool"] == "qwen"


def test_an_ambiguous_client_name_is_not_guessed():
    assert firstcall.harness_of({"name": "claude-gemini-bridge"}, {}) == ""
    assert firstcall.harness_of({"name": "Kilo Code"}, {}) == "kilo"


def test_a_failing_start_degrades_to_no_note_and_never_fails_the_call(repo, monkeypatch):
    monkeypatch.setattr(firstcall, "_session_start", lambda *a, **k: 1 / 0)
    srv = Server(repo)
    srv.client_info = {"name": "some-client"}
    res = _call(srv)
    assert not res.get("isError") and "ddflow session start" not in "".join(_texts(res))

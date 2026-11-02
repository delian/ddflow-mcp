"""The ACP wire ports: the same cases against the default adapters and their fakes (ADP-acp).

`CONFORMANCE` is what the generic contract test (tests/test_ports_contract.py) reads. The
adapter's peer is an in-memory `Channel` driven by a scripted agent: no process, no pipe.
"""

from __future__ import annotations

import collections
import json
import time

import pytest

from ddflow.adapters import acp as A
from ddflow.adapters.fakes.acp import FakeAcpAgent, FakeAcpClient, Script

SCRIPT = Script(
    events=[
        {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "hello"}},
        {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "ls"},
    ],
    asks=[
        {
            "toolCall": {"toolCallId": "t1", "title": "ls"},
            "options": [
                {"optionId": "yes", "name": "Allow", "kind": "allow_once"},
                {"optionId": "no", "name": "Reject", "kind": "reject_once"},
            ],
        }
    ],
)


def _on_prompt(session, text, emit):
    for event in SCRIPT.events:
        emit(event)
    return "end_turn"


class _Peer:
    """A Channel to a scripted agent: the default `AcpStdioAgent` plus one permission ask."""

    def __init__(self, mode="ok"):
        self.mode, self.queue, self.sent_cancel, self.asking = mode, collections.deque(), [], None
        self.agent = A.AcpStdioAgent(
            self.queue.append, on_prompt=_on_prompt, agent_info={"name": "peer"}
        )

    def send(self, line):
        if self.mode == "gone":
            raise BrokenPipeError
        msg = json.loads(line)
        if self.mode in ("silent", "closed"):
            return
        if "method" not in msg and self.asking is not None:  # the client's answer to our ask
            mid, outcome = self.asking, msg["result"]["outcome"]
            self.asking = None
            stop = "end_turn" if outcome.get("outcome") == "selected" else "cancelled"
            self.queue.append(A.encode(A.ok(mid, {"stopReason": stop})))
            return
        if msg.get("method") == "session/cancel":
            self.sent_cancel.append(msg["params"]["sessionId"])
            return
        reply = self.agent.handle(msg)
        if msg.get("method") == "session/prompt":
            self.asking = msg["id"]
            ask = A.request(
                "ask-1", "session/request_permission", {"sessionId": "s1", **SCRIPT.asks[0]}
            )
            self.queue.append(A.encode(ask))
            return
        if reply is not None:
            self.queue.append(A.encode(reply))

    def receive(self, timeout_s):
        if self.queue:
            return self.queue.popleft()
        if self.mode == "closed":
            raise EOFError
        time.sleep(min(timeout_s, 0.005))
        return None

    def close(self):
        self.queue.clear()


def _default(mode="ok"):
    def starter(argv, env):
        if mode == "unavailable":
            raise FileNotFoundError(argv[0])
        return _Peer(mode)

    return A.AcpStdioClient(starter, ["vibe-acp"], poll_s=0.01)


def _fake(mode="ok", cls=FakeAcpClient):
    return cls(FakeAcpAgent(SCRIPT, agent_info={"name": "peer"}), mode=mode)


MAKERS = {"default": _default, "fake": _fake}


@pytest.fixture(params=sorted(MAKERS))
def make(request):
    return MAKERS[request.param]


def _allow(request):
    assert request["options"][0]["optionId"] == "yes"
    return "yes"


def _client_cases(client):
    hello = client.connect(1)
    assert hello.status == A.OK and hello.speaks_acp is True
    assert hello.server["name"] == "peer"
    assert hello.protocol.supported is True and hello.protocol.answered == "1"
    session = client.new_session("/work", [{"name": "ddflow", "command": "ddflow", "args": []}], 1)
    assert session.status == A.OK and session.session_id
    done = client.prompt(session.session_id, "list files", 1, permission=_allow)
    assert done.status == A.OK and done.stop_reason == "end_turn"
    assert [e.kind for e in done.events] == ["message", "tool"]
    assert done.events[0].text == "hello" and done.events[1].text == "ls"
    assert done.permissions == [("ls", "yes")]
    refused = client.prompt(session.session_id, "again", 1)  # no policy: never auto-allow
    assert refused.stop_reason == "cancelled" and refused.permissions == [("ls", "")]
    assert client.cancel(session.session_id) is True
    client.close()
    client.close()


def test_a_client_drives_a_scripted_session_and_maps_the_updates(make):
    _client_cases(make())


def _agent_cases(agent):
    old = agent.handle(A.initialize_request(1, 99))
    assert old["result"]["protocolVersion"] == A.SERVED_VERSIONS[0]
    assert agent.handle({"jsonrpc": "2.0", "method": "session/cancel", "params": {}}) is None
    bad = agent.handle(A.request(2, "session/new", {"cwd": 5}))
    assert bad["error"]["code"] == A.INVALID_PARAMS
    unknown = agent.handle(A.request(3, "session/prompt", {"sessionId": "zz", "prompt": []}))
    assert unknown["error"]["code"] == A.INVALID_PARAMS
    assert agent.handle(A.request(4, "nope/x"))["error"]["code"] == A.METHOD_NOT_FOUND
    new = agent.handle(A.request(5, "session/new", {"cwd": "/w", "mcpServers": []}))
    sid = new["result"]["sessionId"]
    prompt = [{"type": "text", "text": "hi"}]
    ran = agent.handle(A.request(6, "session/prompt", {"sessionId": sid, "prompt": prompt}))
    assert ran["result"]["stopReason"] == "end_turn"


def _agents():
    sent = []
    default = A.AcpStdioAgent(sent.append, on_prompt=_on_prompt)
    return {"default": default, "fake": FakeAcpAgent(SCRIPT)}


@pytest.mark.parametrize("which", ["default", "fake"])
def test_an_agent_negotiates_validates_and_runs_a_prompt(which):
    _agent_cases(_agents()[which])


@pytest.mark.parametrize("which", ["default", "fake"])
def test_the_agent_streams_updates_and_keeps_the_injected_mcp_servers(which):
    agent = _agents()[which]
    servers = [{"name": "ddflow", "command": "ddflow", "args": ["mcp"]}]
    sid = agent.handle(A.request(1, "session/new", {"cwd": "/w", "mcpServers": servers}))["result"][
        "sessionId"
    ]
    assert agent.sessions[sid].mcp_servers == servers and agent.sessions[sid].cwd == "/w"
    agent.handle(A.request(2, "session/prompt", {"sessionId": sid, "prompt": []}))
    agent.handle({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": sid}})
    assert agent.sessions[sid].cancelled is True


def test_the_agent_serves_lines_and_streams_updates_before_the_answer():
    out = []
    agent = A.AcpStdioAgent(out.append, on_prompt=_on_prompt)
    new = A.encode(A.request(1, "session/new", {"cwd": "/w"}))
    ask = A.encode(A.request(2, "session/prompt", {"sessionId": "s1", "prompt": []}))
    agent.serve([new + "\n", ask + "\n"])
    frames = [json.loads(line) for line in out]
    assert [f.get("method") or "answer" for f in frames] == [
        "answer",
        "session/update",
        "session/update",
        "answer",
    ]
    assert frames[-1]["result"]["stopReason"] == "end_turn"


def test_a_cancel_during_a_prompt_makes_the_stop_reason_cancelled():
    agent = A.AcpStdioAgent(lambda line: None, on_prompt=lambda s, t, emit: "end_turn")
    sid = agent.handle(A.request(1, "session/new", {"cwd": "/w"}))["result"]["sessionId"]
    agent.sessions[sid].cancelled = True
    done = agent.handle(A.request(2, "session/prompt", {"sessionId": sid, "prompt": []}))
    assert done["result"]["stopReason"] == "cancelled"
    again = agent.handle(A.request(3, "session/prompt", {"sessionId": sid, "prompt": []}))
    assert again["result"]["stopReason"] == "end_turn"  # the cancel ended one turn only
    for bad in ({"sessionId": []}, ["s1"]):
        assert agent.handle({"jsonrpc": "2.0", "method": "session/cancel", "params": bad}) is None


@pytest.mark.parametrize(
    ("mode", "status", "knows"),
    [
        ("unavailable", A.UNAVAILABLE, False),
        ("gone", A.EOF, False),
        ("closed", A.EOF, False),
        ("silent", A.TIMEOUT, None),
    ],
)
def test_a_client_that_cannot_reach_the_agent_says_so_and_never_claims_it_speaks(
    make, mode, status, knows
):
    hello = make(mode).connect(0.05)
    assert hello.status == status and hello.speaks_acp is knows
    assert hello.protocol.supported is None  # never False for silence


def test_a_client_that_is_not_connected_is_unavailable(make):
    client = make()
    assert client.new_session("/w", [], 1).status == A.UNAVAILABLE
    assert client.prompt("s1", "x", 1).status == A.UNAVAILABLE
    assert client.cancel("s1") is False


def test_a_version_the_wire_does_not_serve_is_a_fact_not_a_failure():
    assert A.negotiate_version(1, 1).supported is True
    other = A.negotiate_version(1, 7)
    assert other.supported is False and other.answered == "7"
    assert A.negotiate_version(1, None).supported is None


def test_an_update_maps_to_a_common_lane_event():
    ev = A.lane_event
    assert ev({"sessionUpdate": "agent_thought_chunk", "content": {"text": "hm"}}).kind == "thought"
    assert ev({"sessionUpdate": "tool_call_update", "toolCallId": "x", "title": "t"}).kind == (
        "tool_update"
    )
    assert ev({"sessionUpdate": "plan", "entries": []}).kind == "plan"
    odd = ev({"sessionUpdate": "brand_new"})
    assert odd.kind == "other" and odd.data["sessionUpdate"] == "brand_new"


def test_the_adapter_and_the_fake_satisfy_the_ports():
    from ddflow.adapters.fakes import FAKES

    assert isinstance(_default(), A.AcpClient) and isinstance(_fake(), A.AcpClient)
    assert isinstance(_agents()["default"], A.AcpAgent) and isinstance(FakeAcpAgent(), A.AcpAgent)
    assert FAKES["AcpClient"] is FakeAcpClient and FAKES["AcpAgent"] is FakeAcpAgent


CONFORMANCE = {
    "AcpClient": {
        "real": lambda monkeypatch: _default(),
        "fake": lambda cls: _fake(cls=cls),
        "cases": _client_cases,
    },
    "AcpAgent": {
        "real": lambda monkeypatch: _agents()["default"],
        "fake": lambda cls: cls(SCRIPT),
        "cases": _agent_cases,
    },
}

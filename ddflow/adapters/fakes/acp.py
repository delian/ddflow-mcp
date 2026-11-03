"""Shipped stand-ins for the ACP wire ports: `FakeAcpAgent` and `FakeAcpClient`.

`FakeAcpAgent` replays a scripted session (`Script`): the same updates on every prompt (the
permission asks are put by the client fake), in memory, no pipe and no process. `FakeAcpClient` calls the agent's `handle`
directly, maps the updates it streamed and answers its asks through the caller's policy.
`FakeAcpClient(mode=...)` scripts the failure a real agent can show: `silent` (timeout,
unknown), `gone` and `closed` (the stream ended) and `unavailable` (it could not start). The
same cases run against these and the default adapters (tests/test_adapter_acp.py).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .. import acp as A
from . import fake


@dataclass
class Script:
    """What a prompt does: the agent streams `events`; the fake CLIENT puts each of `asks` to its
    permission policy (the agent half has no read loop to await an answer)."""

    events: list[dict[str, Any]] = field(default_factory=list)
    asks: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = "end_turn"


@fake
class FakeAcpAgent:
    """An agent that replays a `Script`; records the updates it streamed in `sent`."""

    def __init__(
        self, script: Script | None = None, *, agent_info: dict[str, Any] | None = None
    ) -> None:
        self.script = script or Script()
        self.agent_info = agent_info or {"name": "fake-acp", "version": "0"}
        self.sessions: dict[str, A.AcpSession] = {}
        self.sent: list[dict[str, Any]] = []

    def session(self, session_id: str) -> A.AcpSession | None:
        return self.sessions.get(session_id)

    def handle(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        method, params = msg.get("method"), msg.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        if method == "session/cancel" and "id" in msg:  # a request is always answered
            return A.err(msg["id"], A.INVALID_PARAMS, "session/cancel is a notification")
        if method == "session/cancel":
            sid = params.get("sessionId")
            if isinstance(sid, str) and (session := self.sessions.get(sid)) is not None:
                session.cancelled = session.active
            return None
        if "id" not in msg:
            return None
        if (refusal := A.check_request(msg)) is not None:
            return refusal
        mid = msg["id"]
        if method == "initialize":
            want = params.get("protocolVersion")
            version = (
                want if type(want) is int and want in A.SERVED_VERSIONS else A.SERVED_VERSIONS[0]
            )
            return A.ok(
                mid,
                {"protocolVersion": version, "agentCapabilities": {}, "agentInfo": self.agent_info},
            )
        if method == "session/new":
            return self._new(mid, params)
        if method == "session/prompt":
            return self._prompt(mid, params)
        return A.err(mid, A.METHOD_NOT_FOUND, f"method not found: {method}")

    def _new(self, mid: Any, params: dict[str, Any]) -> dict[str, Any]:
        cwd, servers = params.get("cwd"), params.get("mcpServers", [])
        if not isinstance(cwd, str) or not isinstance(servers, list):
            return A.err(
                mid, A.INVALID_PARAMS, "session/new needs a string `cwd` and a list `mcpServers`"
            )
        sid = f"s{len(self.sessions) + 1}"
        self.sessions[sid] = A.AcpSession(A.OK, session_id=sid, cwd=cwd, mcp_servers=servers)
        return A.ok(mid, {"sessionId": sid})

    def _prompt(self, mid: Any, params: dict[str, Any]) -> dict[str, Any]:
        sid = params.get("sessionId")
        session = self.sessions.get(sid) if isinstance(sid, str) else None
        if session is None:
            return A.err(mid, A.INVALID_PARAMS, f"unknown session: {sid}")
        for update in self.script.events:
            note = {"sessionId": sid, "update": update}
            self.sent.append({"jsonrpc": "2.0", "method": "session/update", "params": note})
        cancelled, session.cancelled = session.cancelled, False
        return A.ok(mid, {"stopReason": A.CANCELLED if cancelled else self.script.stop_reason})

    def serve(self, lines) -> None:
        for raw in lines:
            if raw.strip():
                reply = self.handle(json.loads(raw))
                if reply is not None:
                    self.sent.append(reply)


@fake
class FakeAcpClient:
    """A client wired to a `FakeAcpAgent`, scripted by `mode` (see the module docstring)."""

    def __init__(self, agent: FakeAcpAgent | None = None, *, mode: str = "ok") -> None:
        self.agent = agent or FakeAcpAgent()
        self.mode = mode
        self.connected = False
        self.closed = False
        self.cancels: list[str] = []
        self._id = 0

    def _failure(self) -> A.Outcome | None:
        if self.mode == "silent":
            return A.Outcome(A.TIMEOUT, detail="no answer (scripted)")
        if self.mode in ("gone", "closed"):
            return A.Outcome(A.EOF, detail="the agent closed its output (scripted)")
        return None

    def _ask(self, method: str, params: dict[str, Any]) -> A.Outcome:
        self._id += 1
        reply = self.agent.handle(A.request(self._id, method, params))
        assert reply is not None
        if "error" in reply:
            return A.Outcome(A.ERROR, error=reply["error"])
        return A.Outcome(A.OK, result=reply["result"])

    def connect(self, timeout_s: float, *, version: int = A.SERVED_VERSIONS[0]) -> A.AcpHandshake:
        none = A.negotiate_version(version, None)
        if self.mode == "unavailable":
            return A.AcpHandshake(A.UNAVAILABLE, detail="could not start (scripted)", protocol=none)
        if (bad := self._failure()) is not None:
            return A.AcpHandshake(**vars(bad), protocol=none)
        out = self._ask("initialize", A.initialize_request(0, version)["params"])
        self.connected = out.status == A.OK
        info = out.result.get("agentInfo")
        detail, server = A.describe_answer(
            {"result": {"serverInfo": info if isinstance(info, dict) else {}}}
        )
        proto = A.negotiate_version(version, out.result.get("protocolVersion"))
        return A.AcpHandshake(out.status, out.result, detail=detail, server=server, protocol=proto)

    def new_session(
        self, cwd: str, mcp_servers: list[dict[str, Any]], timeout_s: float
    ) -> A.AcpSession:
        if not self.connected:
            return A.AcpSession(A.UNAVAILABLE, detail="not connected: call connect first")
        out = self._ask("session/new", {"cwd": cwd, "mcpServers": mcp_servers})
        sid = out.result.get("sessionId")
        return A.AcpSession(
            **vars(out),
            session_id=sid if isinstance(sid, str) else "",
            cwd=cwd,
            mcp_servers=mcp_servers,
        )

    def prompt(
        self, session_id, text, timeout_s, *, permission=None, on_event=None
    ) -> A.PromptResult:
        if not self.connected:
            return A.PromptResult(A.UNAVAILABLE, detail="not connected: call connect first")
        mark = len(self.agent.sent)
        out = self._ask(
            "session/prompt", {"sessionId": session_id, "prompt": [{"type": "text", "text": text}]}
        )
        result = A.PromptResult(**vars(out), stop_reason=str(out.result.get("stopReason", "")))
        for note in self.agent.sent[mark:]:
            event = A.lane_event(note["params"]["update"])
            result.events.append(event)
            if on_event is not None:
                on_event(event)
        for ask in self.agent.script.asks:
            chosen = permission(ask) if permission is not None else None
            call = ask.get("toolCall")
            title = str(call.get("title", "")) if isinstance(call, dict) else ""
            result.permissions.append((title, chosen or ""))
            if not chosen:
                result.stop_reason = A.CANCELLED
                break  # a refused ask ends the turn: no further asks are put
        return result

    def cancel(self, session_id: str) -> bool:
        if not self.connected:
            return False
        self.cancels.append(session_id)
        self.agent.handle(
            {"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": session_id}}
        )
        return True

    def close(self) -> None:
        self.closed = True
        self.connected = False

"""Shipped stand-ins for the MCP wire ports: `FakeMcpServer` and `FakeMcpClient`.

In memory, no pipe and no process: the client calls the server's `handle` directly, so a test
of anything that talks MCP (a companion probe, our own server's callers) runs without starting
a real server. `FakeMcpClient(mode=...)` scripts the failure a real server can show: `silent`
(no answer: timeout, unknown), `gone` (the stream closed), `echo` (our request came back) and
`unavailable` (it could not start). The same cases run against these and the default adapters
(tests/test_adapters_mcp.py). Registered in `FAKES` under the port's name once `ports.py`
declares the two ports (see ADP-mcp, waiting on ADP-http's files).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from .. import mcp as W


class FakeMcpServer:
    """A declared tool table answered in memory; records what it was asked and told."""

    def __init__(
        self,
        tools: Iterable[dict[str, Any]] = (),
        results: dict[str, Any] | None = None,
        *,
        instructions: str = "",
        server_info: dict[str, Any] | None = None,
    ) -> None:
        self.tools = list(tools)
        self.results = results or {}  # tool name -> result dict, or an Exception to raise
        self.instructions = instructions
        self.server_info = server_info or {"name": "fake-mcp", "version": "0"}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.sent: list[dict[str, Any]] = []

    def notify(self, frame: dict[str, Any]) -> None:
        self.sent.append(frame)

    def handle(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        if "id" not in msg:
            return None
        mid, method, params = msg["id"], msg.get("method", ""), msg.get("params") or {}
        if method == "initialize":
            want = params.get("protocolVersion")
            version = want if want in W.SERVED_VERSIONS else W.SERVED_VERSIONS[0]
            return W.ok(
                mid,
                {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": self.server_info,
                    "instructions": self.instructions,
                },
            )
        if method == "ping":
            return W.ok(mid, {})
        if method == "tools/list":
            return W.ok(mid, {"tools": self.tools})
        if method != "tools/call":
            return W.err(mid, W.METHOD_NOT_FOUND, f"method not found: {method}")
        name, args = params.get("name"), params.get("arguments", {})
        if not isinstance(name, str) or not isinstance(args, dict):
            return W.err(mid, W.INVALID_PARAMS, "tools/call needs a string `name` and object")
        if name not in {t.get("name") for t in self.tools}:
            return W.err(mid, W.INVALID_PARAMS, f"unknown tool: {name}")
        self.calls.append((name, args))
        result = self.results.get(name, {"content": [], "isError": False})
        if isinstance(result, W.ToolError):
            return W.ok(mid, {"content": [{"type": "text", "text": str(result)}], "isError": True})
        return W.ok(mid, result)

    def serve(self, lines, handle=None, *, intercept=None, after=None) -> None:
        answer = handle or self.handle
        for raw in lines:
            if not raw.strip():
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError as exc:
                self.sent.append(W.err(None, W.PARSE_ERROR, f"parse error: {exc}"))
                continue
            if not isinstance(msg, dict):
                self.sent.append(W.err(None, W.INVALID_PARAMS, "a frame is a JSON object"))
                continue
            if intercept is not None and intercept(msg, self.notify):
                continue
            try:
                reply = answer(msg)
            except Exception as exc:
                reply = W.err(msg.get("id"), W.INTERNAL_ERROR, f"internal error: {exc}")
            if reply is not None:
                self.sent.append(reply)
            if after is not None:
                after()


class FakeMcpClient:
    """A client wired to a `FakeMcpServer`, scripted by `mode` (see the module docstring)."""

    def __init__(self, server: FakeMcpServer | None = None, *, mode: str = "ok") -> None:
        self.server = server or FakeMcpServer()
        self.mode = mode
        self.connected = False
        self.closed = False
        self._id = 0

    def _failure(self) -> W.Outcome | None:
        if self.mode == "silent":
            return W.Outcome(W.TIMEOUT, detail="no answer (scripted)")
        if self.mode == "gone":
            return W.Outcome(W.EOF, detail="the server closed its output (scripted)")
        if self.mode == "echo":
            return W.Outcome(W.ECHOED, detail="the request came back (scripted)")
        return None

    def connect(self, timeout_s: float, *, version: str = W.OFFER_VERSION) -> W.Handshake:
        if self.mode == "unavailable":
            return W.Handshake(W.UNAVAILABLE, detail="could not start (scripted)")
        bad = self._failure()
        if bad is not None:
            return W.Handshake(**vars(bad), protocol=W.Negotiation(version))
        reply = self._ask("initialize", W.initialize_request(0, version)["params"])
        self.connected = True
        if reply.status != W.OK:
            return W.Handshake(**vars(reply), protocol=W.Negotiation(version))
        detail, server = W.describe_answer({"result": reply.result})
        proto = W.negotiate(version, server.get("protocolVersion"))
        return W.Handshake(W.OK, reply.result, detail=detail, server=server, protocol=proto)

    def _ask(self, method: str, params: dict[str, Any] | None) -> W.Outcome:
        self._id += 1
        reply = self.server.handle(W.request(self._id, method, params))
        assert reply is not None
        if "error" in reply:
            return W.Outcome(W.ERROR, error=reply["error"])
        return W.Outcome(W.OK, result=reply["result"])

    def _guarded(self, method: str, params: dict[str, Any] | None) -> W.Outcome:
        if not self.connected:
            return W.Outcome(W.UNAVAILABLE, detail="not connected: call connect first")
        return self._failure() or self._ask(method, params)

    def list_tools(self, timeout_s: float) -> W.Outcome:
        return self._guarded("tools/list", None)

    def call(self, name: str, arguments: dict[str, Any], timeout_s: float) -> W.Outcome:
        return self._guarded("tools/call", {"name": name, "arguments": arguments})

    def close(self) -> None:
        self.closed = True

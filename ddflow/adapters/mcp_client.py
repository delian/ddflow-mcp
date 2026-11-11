"""The MCP client side: start, initialize, list and call over an injected `Starter`.

The wire framing (the JSON-RPC constants, `ok`/`err`/`encode`/`request`/`response_in` and
`Negotiation`) stays in `ddflow.adapters.mcp`, the one home a protocol revision or an SDK
swap touches; this module holds only the client ORM on top of it. Separated from `mcp` so
that neither server nor client alone outgrows the 20 KB adapter ceiling (r-small-files):
`mcp` re-exports every name here so `from ddflow.adapters import mcp as W` keeps working
unchanged.

This layer imports no `subprocess`: `infra.proc` is the one home of it. The client speaks
to a server through an INJECTED `Starter`, so nothing here starts a process itself.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .mcp_wire import (  # the wire an SDK swap touches: one home (no mcp import: keeps the graph a DAG)
    CLOSED_BEFORE_READ,
    ECHOED,
    EOF,
    ERROR,
    OFFER_VERSION,
    OK,
    POLL_S,
    TIMEOUT,
    UNAVAILABLE,
    Negotiation,
    describe_answer,
    encode,
    initialize_request,
    negotiate,
    request,
    response_in,
)
from .ports import port


@runtime_checkable
class Channel(Protocol):
    """One started server, as lines in and lines out."""

    def send(self, line: str) -> None:
        """Write one frame line; raises OSError when the server is gone."""

    def receive(self, timeout_s: float) -> str | None:
        """The next line, None when none came within `timeout_s`; EOFError when closed."""

    def close(self) -> None:
        """Stop the server and everything it started."""


class Starter(Protocol):
    """Start a server and return its `Channel`; raises OSError when it cannot start. Wired
    by the caller from `ddflow.infra.proc` (the one subprocess home)."""

    def __call__(self, argv: list[str], env: dict[str, str] | None) -> Channel: ...


@dataclass
class Outcome:
    """What one client call learned: `status` (see the constants), the JSON-RPC `result` or
    `error` object, and the reading for a person."""

    status: str
    result: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] = field(default_factory=dict)
    detail: str = ""
    elapsed_s: float = 0.0

    @property
    def answered(self) -> bool:
        """The server spoke JSON-RPC (a JSON-RPC error answer counts)."""
        return self.status in (OK, ERROR)


@dataclass
class Handshake(Outcome):
    server: dict[str, Any] = field(default_factory=dict)
    protocol: Negotiation = field(default_factory=lambda: Negotiation(OFFER_VERSION))

    @property
    def speaks_mcp(self) -> bool | None:
        """True answered, False a fact (gone, wrong thing), None could not tell."""
        if self.answered:
            return True
        return None if self.status == TIMEOUT else False


@port
@runtime_checkable
class McpClient(Protocol):
    """Start an MCP server, initialize, list and call its tools, bounded in time.

    Home: `ddflow.adapters.mcp_client` (fake: `FakeMcpClient`). Single adapter. Silence is
    `timeout` (unknown), never a pass; a server that cannot start is `unavailable`.
    """

    def connect(self, timeout_s: float, *, version: str = OFFER_VERSION) -> Handshake:
        """Start the server and `initialize`; the `Handshake` says what happened."""

    def list_tools(self, timeout_s: float) -> Outcome:
        """`tools/list`; the tool dicts are in `result["tools"]`."""

    def call(self, name: str, arguments: dict[str, Any], timeout_s: float) -> Outcome:
        """`tools/call`; the tool's result is in `result`."""

    def close(self) -> None:
        """Stop the server; safe to call twice."""


class _ChannelClient:
    """The channel plumbing shared by the stdio clients (mcp `StdioClient`, the ACP client)."""

    _channel: Channel | None

    def close(self) -> None:
        channel, self._channel = self._channel, None
        if channel is not None:
            channel.close()

    def _send(self, line: str) -> bool:
        return send_line(self._channel, line)


class StdioClient(_ChannelClient):
    """The default `McpClient`, over an injected `Starter`."""

    def __init__(
        self,
        starter: Starter,
        argv: list[str],
        env: dict[str, str] | None = None,
        *,
        poll_s: float = POLL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._starter, self._argv, self._env = starter, argv, env
        self._poll, self._clock = poll_s, clock
        self._channel: Channel | None = None
        self._next_id = 0

    def connect(self, timeout_s: float, *, version: str = OFFER_VERSION) -> Handshake:
        t0 = self._clock()
        try:
            self._channel = self._starter(self._argv, self._env)
        except OSError as exc:
            return Handshake(
                UNAVAILABLE,
                detail=f"could not start {self._argv[:1]}: {exc}",
                protocol=Negotiation(version),
            )
        self._next_id = 1
        first = self._exchange(initialize_request(1, version), timeout_s, t0)
        hello = Handshake(**vars(first), protocol=Negotiation(version))
        if first.status == OK:
            hello.detail, hello.server = describe_answer({"result": first.result})
            hello.protocol = negotiate(version, first.result.get("protocolVersion"))
            if not self._send(
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
            ):
                hello.detail += "; closed before `notifications/initialized` (it did answer)"
        elif first.status == ERROR:
            hello.detail = describe_answer({"error": first.error})[0]
        return hello

    def list_tools(self, timeout_s: float) -> Outcome:
        return self._ask("tools/list", None, timeout_s)

    def call(self, name: str, arguments: dict[str, Any], timeout_s: float) -> Outcome:
        return self._ask("tools/call", {"name": name, "arguments": arguments}, timeout_s)

    def _ask(self, method: str, params: dict[str, Any] | None, timeout_s: float) -> Outcome:
        if self._channel is None:
            return Outcome(UNAVAILABLE, detail="not connected: call connect first")
        self._next_id += 1
        return self._exchange(request(self._next_id, method, params), timeout_s, self._clock())

    def _exchange(self, frame: dict[str, Any], timeout_s: float, t0: float) -> Outcome:
        """Send `frame` and read until its answer, the deadline or the end of the stream."""
        mid, deadline = frame["id"], t0 + timeout_s

        def done(status: str, **kw: Any) -> Outcome:
            return Outcome(status, elapsed_s=round(self._clock() - t0, 2), **kw)

        if not self._send(encode(frame)):
            return done(EOF, detail=CLOSED_BEFORE_READ)
        echoed = False
        while True:
            left = deadline - self._clock()
            if left <= 0:
                return done(
                    ECHOED if echoed else TIMEOUT, detail=f"no answer within {timeout_s:g}s"
                )
            try:
                line = self._channel.receive(min(left, self._poll))  # type: ignore[union-attr]
            except EOFError:
                return done(EOF, detail="the server closed its output")
            if line is None:
                continue
            msg = response_in(line, mid)
            if msg is None:
                echoed = echoed or _is_echo(line, frame)
                continue
            return finish_answer(done, msg)


def send_line(channel, line: str) -> bool:
    """Write `line` down `channel` when it is connected; False when it is not, or the write fails."""
    if channel is None:
        return False
    try:
        channel.send(line)
        return True
    except OSError:
        return False


def finish_answer(done, msg: dict[str, Any]) -> Outcome:
    """The `done` verdict for a parsed answer: its object `result` or `error`, or ``{}``."""
    if "result" in msg:
        return done(OK, result=msg["result"] if isinstance(msg["result"], dict) else {})
    return done(ERROR, error=msg["error"] if isinstance(msg["error"], dict) else {})


def _is_echo(line: str, frame: dict[str, Any]) -> bool:
    """Our own request handed back (`cat`): recognisable by its id and method."""
    try:
        msg = json.loads(line)
    except ValueError:
        return False
    return (
        isinstance(msg, dict)
        and msg.get("id") == frame["id"]
        and msg.get("method") == frame["method"]
    )

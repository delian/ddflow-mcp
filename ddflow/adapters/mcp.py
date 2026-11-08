"""The MCP wire: ONE adapter for JSON-RPC framing, the server side and the client side.

A protocol revision or an SDK swap touches this module (and `ddflow.surfaces.mcp_protocol`,
which holds the revision rules), nothing else. Two ports, both declared here next to their
default adapter (D-adapters-answers):

* `McpServer` -- the server transport: read newline-delimited JSON-RPC, map a bad frame and
  an escaped exception to JSON-RPC errors, write replies and notifications under one lock,
  and answer a DECLARED tool table (`initialize`, `ping`, `tools/list`, `tools/call`).
* `McpClient` -- start a server through an INJECTED `Starter` (this layer imports no
  `subprocess`: `infra.proc` is the one home of it), `initialize`, list and call tools, with
  timeout and unavailable semantics.

Pure standard library; imports no other ddflow layer (`.ports`, the stdlib-only marker, is
the same layer). Stand-ins `FakeMcpServer`/`FakeMcpClient` live in `ddflow.adapters.fakes.mcp`;
the same cases run against both (tests/test_adapter_mcp.py).
"""

from __future__ import annotations

import json
import math
import threading
import time
import traceback
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .ports import port

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND = -32700, -32600, -32601
INVALID_PARAMS, INTERNAL_ERROR = -32602, -32603
UNSUPPORTED_PROTOCOL_VERSION = -32022
#: The protocol revision a client offers when its caller names none: any server answering
#: proves it speaks MCP, so the exact value is not what is tested.
OFFER_VERSION = "2025-03-26"
#: The newest legacy revisions this wire serves, newest first (`ddflow.surfaces.mcp_protocol`
#: adds the era rules and takes this tuple as `SUPPORTED_PROTOCOLS`: one list).
SERVED_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
#: What a client waits for one line before looking at its deadline again.
POLL_S = 0.5
#: The `detail` of an `eof` outcome when the request could not even be written.
CLOSED_BEFORE_READ = "the server closed before reading the request"
#: A client reports a request: `ok` answered, `error` an error answer, `timeout` no answer,
#: `eof` closed, `echoed` our request back, `unavailable` the server could not start.
OK, ERROR, TIMEOUT, EOF, ECHOED, UNAVAILABLE = (
    "ok",
    "error",
    "timeout",
    "eof",
    "echoed",
    "unavailable",
)


def ok(mid: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def err(mid: Any, code: int, message: str, *, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": mid, "error": error}


#: The refusal of a `tools/call` whose `name` or `arguments` has the wrong shape (shared with the fake).
BAD_CALL = "tools/call needs a string `name` and object `arguments`"


def valid_id(mid: Any) -> bool:
    """A JSON-RPC id is a string, a finite number or null; a boolean and NaN are none."""
    if isinstance(mid, bool):
        return False
    if isinstance(mid, float):
        return math.isfinite(mid)
    return mid is None or isinstance(mid, str | int)


def check_request(msg: dict[str, Any]) -> dict[str, Any] | None:
    """The JSON-RPC error that refuses `msg` as a request, or None when it is well formed
    (a notification, having no id, is never refused: it is never answered). The server and
    its fake share it, so the validation is written once."""
    if "id" not in msg:
        return None
    mid = msg["id"]
    if not valid_id(mid):
        return err(None, INVALID_REQUEST, "`id` must be a string, a number or null")
    if msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return err(mid, INVALID_REQUEST, "not a JSON-RPC 2.0 request")
    if msg.get("params") is not None and not isinstance(msg["params"], dict):
        return err(mid, INVALID_PARAMS, "`params` must be an object")
    return None


def encode(frame: dict[str, Any]) -> str:
    """One frame as a line (no newline): the only encoder, so framing changes in one place.
    `json.dumps` defaults are kept on purpose (no behaviour change from the hand-rolled loop)."""
    return json.dumps(frame)


def request(mid: Any, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    frame: dict[str, Any] = {"jsonrpc": "2.0", "id": mid, "method": method}
    if params is not None:
        frame["params"] = params
    return frame


def initialize_request(
    mid: Any = 1, version: str = OFFER_VERSION, name: str = "ddflow-verify"
) -> dict[str, Any]:
    return request(
        mid,
        "initialize",
        {
            "protocolVersion": version,
            "capabilities": {},
            "clientInfo": {"name": name, "version": "0"},
        },
    )


def response_in(line: str | bytes, want_id: Any) -> dict | None:
    """`line` as the response to `want_id`, or None (not JSON, not a response, other id)."""
    try:
        msg = json.loads(line)
    except ValueError:
        return None
    if (
        isinstance(msg, dict)
        and msg.get("jsonrpc") == "2.0"
        and msg.get("id") == want_id
        and "method" not in msg
        and ("result" in msg or "error" in msg)
    ):
        return msg
    return None


def describe_answer(msg: dict) -> tuple[str, dict]:
    """(detail, server facts) for a JSON-RPC response to `initialize`."""
    result = msg.get("result")
    if not isinstance(result, dict):
        return "answered initialize with a JSON-RPC error (it speaks the protocol)", {}
    info = result.get("serverInfo")
    server = dict(info) if isinstance(info, dict) else {}
    if result.get("protocolVersion"):
        server["protocolVersion"] = result["protocolVersion"]
    who = " ".join(str(server[k]) for k in ("name", "version") if server.get(k))
    return f"answered initialize ({who or 'no serverInfo'})", server


@dataclass(frozen=True)
class Negotiation:
    """The protocol-version fact of one `initialize`, three-valued as the driver descriptors
    record theirs: `supported` is True (the server answered a revision this wire serves),
    False (it answered one this wire does not serve: a FACT, not a failure) or None (no
    answer, so unknown). Never `False` for silence."""

    offered: str
    answered: str = ""
    supported: bool | None = None


def negotiate(offered: str, answered: Any, served: Iterable[str] = SERVED_VERSIONS) -> Negotiation:
    if not isinstance(answered, str) or not answered:
        return Negotiation(offered)
    return Negotiation(offered, answered, answered in tuple(served))


# -- the server side ---------------------------------------------------------------------


class ToolError(Exception):
    """Raised by a tool handler: becomes a tool result with `isError`, never a JSON-RPC error
    (a bad argument is the tool's answer, SEP-1303)."""


Handler = Callable[[dict[str, Any]], "dict[str, Any] | None"]


@port
@runtime_checkable
class McpServer(Protocol):
    """Serve MCP over newline-delimited JSON-RPC, a declared tool table or a caller's handler.

    Home: `ddflow.adapters.mcp` (fake: `FakeMcpServer`). Single adapter.
    """

    def notify(self, frame: dict[str, Any]) -> None:
        """Write one server-initiated frame to the client."""

    def handle(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        """Answer one parsed message from the declared table; None for a notification."""

    def serve(
        self,
        lines: Iterable[str],
        handle: Handler | None = None,
        *,
        intercept: Callable[..., bool] | None = None,
        after: Callable[[], None] | None = None,
    ) -> None:
        """Read frames until the input ends, answering each through `handle` (default: this
        table)."""


class StdioServer:
    """The default `McpServer`: a declared tool table behind a line-framed transport.

    ddflow's own `Server` passes its own `handle` to `serve` (its era rules stay as they were;
    `check_request` is not applied to it: that would change behaviour). `tools` is the table
    already filtered to the profile in force (declared once, in `surfaces/registry`);
    `call(name, arguments)` returns the tool's result dict and may raise `ToolError`; `write`
    takes one encoded line.
    """

    def __init__(
        self,
        write: Callable[[str], None] | None = None,
        *,
        tools: Iterable[dict[str, Any]] = (),
        call: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
        instructions: str = "",
        server_info: dict[str, Any] | None = None,
        versions: tuple[str, ...] = SERVED_VERSIONS,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._write = write or (lambda line: None)
        self._lock = threading.Lock()
        self.tools = list(tools)
        self._call = call
        self.instructions = instructions
        self.server_info = server_info or {"name": "ddflow", "version": "0"}
        self.versions = versions
        self._log = log or (lambda text: None)

    def notify(self, frame: dict[str, Any]) -> None:
        with self._lock:  # a heartbeat thread and the loop share the stream
            self._write(encode(frame))

    def handle(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        if "id" not in msg:
            return None  # a notification is never answered
        mid, method = msg["id"], msg.get("method")
        if (refusal := check_request(msg)) is not None:
            return refusal
        params = msg.get("params") or {}
        if method == "initialize":
            want = params.get("protocolVersion")
            version = want if want in self.versions else self.versions[0]
            return ok(
                mid,
                {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": self.server_info,
                    "instructions": self.instructions,
                },
            )
        if method == "ping":
            return ok(mid, {})
        if method == "tools/list":
            return ok(mid, {"tools": self.tools})
        if method == "tools/call":
            return self._tools_call(mid, params)
        return err(mid, METHOD_NOT_FOUND, f"method not found: {method}")

    def _tools_call(self, mid: Any, params: dict[str, Any]) -> dict[str, Any]:
        name, args = params.get("name"), params.get("arguments", {})
        if not isinstance(name, str) or not isinstance(args, dict):
            return err(mid, INVALID_PARAMS, BAD_CALL)
        if self._call is None or name not in {t.get("name") for t in self.tools}:
            return err(mid, INVALID_PARAMS, f"unknown tool: {name}")
        try:
            return ok(mid, self._call(name, args))
        except ToolError as exc:
            return ok(mid, {"content": [{"type": "text", "text": str(exc)}], "isError": True})

    def serve(
        self,
        lines: Iterable[str],
        handle: Handler | None = None,
        *,
        intercept: Callable[..., bool] | None = None,
        after: Callable[[], None] | None = None,
    ) -> None:
        """Newline-delimited JSON-RPC until the input ends.

        A line that is not JSON is `-32700`; an exception out of `handle` is `-32603` with
        the traceback to `log` (never to the stream: a stray write corrupts it).
        `intercept(msg, notify)` may take a message over (an offloaded call): True means
        it answered itself. `after()` runs after every parsed message, taken over or answered."""
        handler = handle or self.handle
        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as exc:
                self.notify(err(None, PARSE_ERROR, f"parse error: {exc}"))
                continue
            if not isinstance(msg, dict):
                self.notify(err(None, INVALID_REQUEST, "a frame is a JSON object"))
                continue
            if intercept is None or not intercept(msg, self.notify):
                try:
                    reply = handler(msg)
                except Exception as exc:
                    self._log(traceback.format_exc())
                    reply = err(msg.get("id"), INTERNAL_ERROR, f"internal error: {exc}")
                    if "id" not in msg:
                        reply = None  # a notification is never answered, even when it fails
                if reply is not None:
                    self.notify(reply)
            if after is not None:
                after()


# -- the client side ---------------------------------------------------------------------


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

    Home: `ddflow.adapters.mcp` (fake: `FakeMcpClient`). Single adapter. Silence is
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

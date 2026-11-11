"""The MCP wire: JSON-RPC framing plus the server side; the client side is `mcp_client`.

A protocol revision or an SDK swap touches this module (and `ddflow.surfaces.mcp_protocol`,
which holds the revision rules), nothing else. The CLIENT ORM (`McpClient`, its
`StdioClient`, `Channel`/`Starter`/`Outcome`) lives in `ddflow.adapters.mcp_client` and is
re-exported at the bottom of this module so the split is invisible to importers; neither
side alone outgrows the 20 KB adapter ceiling (r-small-files).

One port here next to its default adapter (D-adapters-answers):

* `McpServer` -- the server transport: read newline-delimited JSON-RPC, map a bad frame and
  an escaped exception to JSON-RPC errors, write replies and notifications under one lock,
  and answer a DECLARED tool table (`initialize`, `ping`, `tools/list`, `tools/call`).

`McpClient` in `mcp_client` does the same from the other side: it starts a server through
an INJECTED `Starter` (that layer imports no `subprocess`: `infra.proc` is the one home of
it), initializes, lists and calls tools, with timeout and unavailable semantics.

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
# The client ORM lives in `ddflow.adapters.mcp_client` (imported at the bottom): separating
# it keeps neither side alone at the 20 KB adapter ceiling (r-small-files). Every name the
# client module defines is re-exported here so `from ddflow.adapters import mcp as W` and
# `from .mcp import McpClient` keep resolving unchanged (the ACP client imports them too).

from .mcp_client import (  # noqa: E402 -- the client side, re-exported for the callers above
    _ChannelClient,
    _is_echo,
    Channel,
    Handshake,
    McpClient,
    Outcome,
    Starter,
    StdioClient,
    finish_answer,
    send_line,
)

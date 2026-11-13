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
import threading
import traceback
from collections.abc import Callable, Iterable
from typing import Any, Protocol, runtime_checkable

from .mcp_wire import (  # noqa: F401 -- the wire framing, re-exported; mcp_client reads it from mcp_wire
    BAD_CALL,
    CLOSED_BEFORE_READ,
    ECHOED,
    EOF,
    ERROR,
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    OFFER_VERSION,
    OK,
    PARSE_ERROR,
    POLL_S,
    SERVED_VERSIONS,
    TIMEOUT,
    UNAVAILABLE,
    UNSUPPORTED_PROTOCOL_VERSION,
    Negotiation,
    check_request,
    describe_answer,
    encode,
    err,
    initialize_request,
    negotiate,
    ok,
    request,
    response_in,
    valid_id,
)
from .ports import port

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
# it keeps neither side alone at the 20 KB adapter ceiling (r-small-files). The names below
# are re-exported here only so `from ddflow.adapters import mcp as W` and `from .mcp import
# McpClient` keep resolving unchanged (the ACP client imports them too).

from .mcp_client import (  # noqa: E402, F401 -- re-exported, not used locally
    Channel,
    Handshake,
    McpClient,
    Outcome,
    Starter,
    StdioClient,
    _ChannelClient,
    finish_answer,
    send_line,
)

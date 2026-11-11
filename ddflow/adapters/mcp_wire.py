"""The MCP wire framing, separated from the server (`mcp`) and client (`mcp_client`).

The JSON-RPC constants, the `ok`/`err`/`encode`/`request`/`response_in`/`initialize_request`
/`describe_answer` helpers and `Negotiation` are the one home a protocol revision or an SDK
swap touches. `mcp` (server) and `mcp_client` (client) both import them from here, so neither
imports the other: `mcp` re-exports the client names at its bottom, and `mcp_client` reads the
wire from here, which leaves a DAG (no cycle) while the public surface `from ddflow.adapters
import mcp as W` keeps resolving unchanged.

Pure standard library; imports no ddflow layer (`.ports`, the stdlib-only marker, is the same
layer).
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

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
        and not isinstance(msg.get("id"), bool)  # `True == 1`: a boolean id never matches a numeric one
        and not isinstance(want_id, bool)  # and a boolean `want_id` never matches a numeric id either
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

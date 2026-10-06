"""The MCP protocol engine: which revisions are served, and how a reply is shaped for each.

Protocol churn, not libraries, is the maintenance risk (R-unify): every revision so far
has moved the handshake, the envelope or the metadata. This module is the ONE place that
knows about revisions. `surfaces/mcp.py` routes methods to ddflow and the tools in
`surfaces/tools/` answer them; neither sees which revision a request came in under. A
new revision is a change here plus its official schema under `tests/fixtures/mcp-schema/`,
which `tests/test_mcp_protocol_conformance.py` replays every reply against.

Two eras, one engine (B149, D-unify 8):

* LEGACY (`SUPPORTED_PROTOCOLS`, newest 2025-11-25): negotiated once, by `initialize`,
  for the life of the connection (`legacy_version`).
* MODERN (`MODERN_PROTOCOLS`, 2026-07-28): stateless. Every request names its version and
  the client's capabilities in `params._meta` (`modern_check`), discovery is
  `server/discover` (`discover`), every result carries `resultType`, the server's identity
  and, for list/read results, cache hints (`modernize`). A request may need more input from
  the client: the server answers `InputRequiredResult` and the client retries with
  `inputResponses` and the `requestState` it was given (multi round-trip, `input_required`
  and `round_trip`).

Stdlib only, deliberately: the official SDK pulls 28 packages, and stdio is a few hundred
lines of newline-delimited JSON-RPC. The `[mcp-sdk]` extra is for a network transport
behind the same tool registry, never for this.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import secrets
import time
from typing import Any

# Absolute on purpose: `ddflow/__init__.py` is the one declaration of the version and has no
# dependencies, so this cannot cycle; a relative `from .. import` would read as a layer.
# A plain import, never importlib.metadata: installed metadata is stale in a source tree.
from ddflow import __version__ as _VERSION

#: The LEGACY revisions: negotiated once, by `initialize`, for the life of the process.
#: Newest first: an unknown version is answered with the first. `2025-11-25`
#: (Bac0bb04c9f) asks nothing new of a stdio server offering tools, resources and
#: prompts: everything it adds is optional (icons, tasks, URL elicitation, sampling with
#: tools, `Implementation.description`) or already done here -- a bad argument is a tool
#: result with `isError` (SEP-1303), tool names keep to `[A-Za-z0-9_.-]{1,128}`
#: (SEP-986), and the input schemas name no `$schema`, so they read as JSON Schema
#: 2020-12, its default dialect (SEP-1613).
SUPPORTED_PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "ddflow", "version": _VERSION, "title": "ddflow work-queue kernel"}

# -- the modern (stateless) revisions ---------------------------------------------------
#
# From `2026-07-28` there is no handshake: every request names its protocol version and
# the client's capabilities in `params._meta`, and the server answers each on its own.
# This server is DUAL-ERA, as that revision allows: a request carrying the modern `_meta`
# is served by the modern rules below, and `initialize` still selects the legacy ones, so
# no client that works today stops working (B149).
#
# Adding the version string is not what honours the revision; its MUSTs are: answer
# `server/discover`; refuse a version it does not serve with -32022 naming the ones it
# does; refuse a modern request missing a required `_meta` field with -32602; give every
# result a `resultType`; and give list/read results their cache hints. Its SHOULD -- the
# server's identity in each result's `_meta` -- is done too. Nothing here sends
# `notifications/message`, a server-initiated request, or the retired -32002.

#: Served per request, statelessly.
MODERN_PROTOCOLS = ("2026-07-28",)
META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_CAPS = "io.modelcontextprotocol/clientCapabilities"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"
#: The caller's agent name in a modern request's `params._meta` (D-mcp-identity-per-call):
#: with no connection to declare it on, a stateless request names its caller itself --
#: here, or with the `as_agent` argument, which wins when both are given.
META_AGENT = "ddflow/agent"
UNSUPPORTED_PROTOCOL_VERSION = -32022
INVALID_PARAMS = -32602

#: `ttlMs` per cacheable method (`CacheableResult`). The tool list and the resource list
#: are fixed for the life of the process (`listChanged` is false), so a client may keep
#: them an hour; everything else is read from the repository's live state -- the prompt
#: list includes the operator's macros, discovery carries instructions that name the
#: work left over from a crash -- so it is fresh on every read. `private` throughout: the
#: content is one operator's repository, never something a shared cache should hold.
CACHE_TTL_MS = {
    "tools/list": 3_600_000,
    "resources/list": 3_600_000,
    "resources/read": 0,
    "prompts/list": 0,
    "server/discover": 0,
}
CACHE_SCOPE = "private"


def ok(mid: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def err(mid: Any, code: int, message: str, *, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": mid, "error": error}


def capabilities() -> dict[str, Any]:
    """What this server offers, for `initialize` and `server/discover` alike."""
    return {
        "tools": {"listChanged": False},
        "resources": {"listChanged": False},
        # Prompts are how a client surfaces a workflow as a slash command. Omitting the
        # capability means a spec-respecting client never calls prompts/list, so the
        # commands exist and are unreachable — which is indistinguishable, from the
        # operator's side, from not having written them.
        "prompts": {"listChanged": False},
    }


def legacy_version(want: Any) -> str:
    """The revision an `initialize` asking for `want` is served: that one when it is
    served, else the newest. Refusing an unknown version outright breaks every client that
    ships ahead of us, which for a tool meant to work with five agents is the likelier
    direction of drift."""
    return want if want in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]


def initialize_result(version: str, instructions: str) -> dict[str, Any]:
    """The `initialize` result for a negotiated legacy `version`."""
    return {
        "protocolVersion": version,
        "capabilities": capabilities(),
        "serverInfo": SERVER_INFO,
        "instructions": instructions,
    }


def discover(instructions: str) -> dict[str, Any]:
    """`server/discover`: the versions, capabilities, identity and instructions a modern
    client would otherwise have learned from `initialize`. `resultType`, the identity in
    `_meta` and the cache hints are added by `modernize`, like every modern result's."""
    return {
        "supportedVersions": [*MODERN_PROTOCOLS, *SUPPORTED_PROTOCOLS],
        "capabilities": capabilities(),
        "instructions": instructions,
    }


def modern_check(msg: dict[str, Any]) -> str | dict[str, Any] | None:
    """The modern version a message is served under, the error that refuses it, or None.

    None: no `io.modelcontextprotocol/protocolVersion` in `params._meta`, so a legacy
    message, served as one -- except `server/discover`, which exists only in the modern
    era: answering it "method not found" would tell a dual-era client's probe that this
    is a legacy server, so a discovery without the version is refused as malformed.

    A version this server does not serve per request is -32022 naming every version it
    supports (the legacy ones through `initialize`, as the spec's dual-era example does);
    a modern request without its required `clientCapabilities` is -32602, and so is a
    retry whose `requestState` this server did not issue for it (`round_trip`).
    """
    params = msg.get("params")
    meta = params.get("_meta") if isinstance(params, dict) else None
    mid = msg.get("id")
    if not isinstance(meta, dict) or META_VERSION not in meta:
        if msg.get("method") == "server/discover":
            return err(mid, INVALID_PARAMS, f"server/discover requires _meta {META_VERSION!r}")
        return None
    want = meta[META_VERSION]
    if not isinstance(want, str):
        return err(mid, INVALID_PARAMS, f"_meta {META_VERSION!r} must be a string")
    if want not in MODERN_PROTOCOLS:
        return err(
            mid,
            UNSUPPORTED_PROTOCOL_VERSION,
            "Unsupported protocol version",
            data={"supported": [*MODERN_PROTOCOLS, *SUPPORTED_PROTOCOLS], "requested": want},
        )
    if not isinstance(meta.get(META_CLIENT_CAPS), dict):
        return err(mid, INVALID_PARAMS, f"a {want} request requires _meta {META_CLIENT_CAPS!r}")
    if isinstance(params, dict) and "requestState" in params:
        try:
            round_trip(params, msg.get("method", ""))
        except ValueError as exc:
            return err(mid, INVALID_PARAMS, f"requestState refused: {exc}")
    return want


def modernize(method: str, reply: dict[str, Any] | None) -> dict[str, Any] | None:
    """A reply as the modern revision shapes it: every result carries `resultType`
    ("complete" unless `input_required` said otherwise) and the server's identity in
    `_meta`; a cacheable method's result carries `ttlMs` and `cacheScope`. Errors and
    silence pass through unchanged."""
    if reply is None or not isinstance(reply.get("result"), dict):
        return reply
    res = reply["result"]
    res.setdefault("resultType", "complete")
    meta = res.get("_meta")
    res["_meta"] = {**(meta if isinstance(meta, dict) else {}), META_SERVER_INFO: dict(SERVER_INFO)}
    if method in CACHE_TTL_MS:
        res["ttlMs"] = CACHE_TTL_MS[method]
        res["cacheScope"] = CACHE_SCOPE
    return reply


# -- multi round-trip (2026-07-28, MRTR) --------------------------------------------------
#
# A request that needs more from the client is answered with an `InputRequiredResult`:
# what to ask (`inputRequests`, each an elicitation, a sampling or a roots request) and an
# opaque `requestState` the client echoes on its retry. The state passes through the
# client, so the spec makes it attacker-controlled input: it is sealed here with an HMAC
# and bound to the method, the request's salient parameters, the caller's `_meta` agent
# and an expiry, and a retry whose state fails any of those is refused (`modern_check`).
# The key is per server process; an offloaded worker is handed its parent's
# (`state_key`), so a state survives the worker that issued it, never the server.
#
# The legacy revisions have no such result -- they asked with server-initiated requests,
# which ddflow never sent -- so this is modern-only by construction.

#: The only methods that MAY answer `InputRequiredResult`.
INPUT_REQUIRED_METHODS = ("prompts/get", "resources/read", "tools/call")
#: The client capability each kind of input request needs declared.
_INPUT_CAPABILITY = {
    "elicitation/create": "elicitation",
    "sampling/createMessage": "sampling",
    "roots/list": "roots",
}
#: How long an issued `requestState` is honoured.
STATE_TTL_S = 600

#: This process's key, in a holder so a worker can adopt its parent's (`state_key`).
_KEY = {"state": secrets.token_bytes(32)}


def state_key(key: bytes | None = None) -> bytes:
    """This process's `requestState` key; given one, adopt it (an offloaded worker)."""
    if key is not None:
        _KEY["state"] = key
    return _KEY["state"]


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(data: bytes) -> bytes:
    return hmac.new(_KEY["state"], data, "sha256").digest()


def _binding(params: dict[str, Any], method: str) -> str:
    """What a state is bound to: the method, its salient parameters and the caller."""
    salient = {k: params.get(k) for k in ("name", "arguments", "uri")}
    meta = params.get("_meta")
    who = meta.get(META_AGENT, "") if isinstance(meta, dict) else ""
    body = json.dumps([method, salient, who], sort_keys=True, separators=(",", ":"), default=str)
    return _b64(_mac(body.encode()))


def input_required(
    method: str,
    params: dict[str, Any],
    input_requests: dict[str, dict[str, Any]] | None = None,
    *,
    state: Any = None,
    ttl_s: int = STATE_TTL_S,
) -> dict[str, Any]:
    """An `InputRequiredResult` for a modern request `method` with `params`: the input
    requests the client must answer and/or `state` (any JSON value), sealed, for the
    retry. Raises ValueError for what the spec forbids: a method that may not ask, an
    empty result, or a request kind the client did not declare it can answer."""
    if method not in INPUT_REQUIRED_METHODS:
        raise ValueError(f"{method} may not answer InputRequiredResult")
    if not input_requests and state is None:
        raise ValueError("an InputRequiredResult carries inputRequests, requestState or both")
    meta = params.get("_meta")
    caps = meta.get(META_CLIENT_CAPS) if isinstance(meta, dict) else None
    caps = caps if isinstance(caps, dict) else {}
    for key, req in (input_requests or {}).items():
        need = _INPUT_CAPABILITY.get(req.get("method", ""))
        if need is None:
            raise ValueError(
                f"input request {key!r}: {req.get('method')!r} is not one a client answers"
            )
        if need not in caps:
            raise ValueError(f"input request {key!r} needs the client capability {need!r}")
    result: dict[str, Any] = {"resultType": "input_required"}
    if input_requests:
        result["inputRequests"] = input_requests
    payload = {"b": _binding(params, method), "x": int(time.time()) + ttl_s, "s": state}
    raw = json.dumps(payload, separators=(",", ":"), default=str).encode()
    result["requestState"] = f"{_b64(raw)}.{_b64(_mac(raw))}"
    return result


def round_trip(params: dict[str, Any], method: str) -> Any:
    """The state a retry carries back, verified; None when it carries none. Raises
    ValueError when the state is not one this server issued for this request and caller,
    or has expired."""
    token = params.get("requestState")
    if token is None:
        return None
    if not isinstance(token, str) or token.count(".") != 1:
        raise ValueError("not a state this server issued")
    body, tag = token.split(".")
    try:
        raw, mac = _unb64(body), _unb64(tag)
    except (binascii.Error, ValueError):
        raise ValueError("not a state this server issued") from None
    if not hmac.compare_digest(mac, _mac(raw)):
        raise ValueError("not a state this server issued")
    payload = json.loads(raw)
    if not hmac.compare_digest(payload["b"], _binding(params, method)):
        raise ValueError("issued for a different request or caller")
    if time.time() > payload["x"]:
        raise ValueError("expired; send the request again without it")
    return payload["s"]

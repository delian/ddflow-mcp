"""The shim<->worker protocol (SU-worker-proto): newline-delimited JSON frames over stdio pipes.

The worker is a child process of the shim (D-soft-upgrade-architecture); the shim owns the
client's connection and forwards each MCP message as a `req` frame carrying the connection's
state (protocol, client_info, agent, called_from), so the worker is stateless per request and
can be replaced under a live connection.

Frames (every one has `t`, the kind):

    hello    shim -> worker  {shim_proto, state_key?}       first frame
    hello    worker -> shim  {worker_proto, digest, version} the reply, or `error`
    req      shim -> worker  {id, msg, conn}                  one MCP message
    res      worker -> shim  {id, msg}                        its answer (`msg` null: none owed)
    note     worker -> shim  {id, msg}                        a frame sent while serving `id`
                                                              (`id` null: not tied to a request)
    drain    shim -> worker  {}                               finish what runs, take nothing new
    drained  worker -> shim  {}                               nothing is running
    bye      either          {reason?}                        the last frame
    error    either          {code, message, id?}             a typed refusal

Versioning: `PROTO_MAJOR` changes when a frame stops meaning what it did; an unknown extra field
(a newer minor) is ignored. A worker refuses a shim of another major with
`incompatible_shim_proto`; the shim (SU-shim) refuses a worker of another major with
`incompatible_worker_proto` by comparing the `worker_proto` of the worker's hello with its
own: either is a HARD upgrade (D-soft-upgrade-rule, rung 5).

Stdlib only: the shim imports this module and must stay small.
"""

from __future__ import annotations

import json
from typing import Any

PROTO_MAJOR = 1
PROTO_MINOR = 0
KINDS = ("hello", "req", "res", "note", "drain", "drained", "bye", "error")


class ProtocolError(ValueError):
    """A frame that is not one: `code` is the typed reason an `error` frame carries."""

    def __init__(self, code: str, message: str, id: Any = None):
        super().__init__(message)
        self.code = code
        self.id = id


def _int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


# kind -> (field, check) for the fields that must be present and well-typed
_REQUIRED = {
    "req": (
        ("id", _int),
        ("msg", lambda v: isinstance(v, dict)),
        ("conn", lambda v: isinstance(v, dict)),
    ),
    "res": (("id", _int), ("msg", lambda v: v is None or isinstance(v, dict))),
    "note": (
        ("id", lambda v: v is None or _int(v)),
        ("msg", lambda v: isinstance(v, dict)),
    ),
    "error": (("code", lambda v: isinstance(v, str)), ("message", lambda v: isinstance(v, str))),
}


def encode(frame: dict[str, Any]) -> str:
    return json.dumps(frame, separators=(",", ":")) + "\n"


def decode(line: str) -> dict[str, Any] | None:
    """The frame in one line; None for a blank line; `ProtocolError` for anything else."""
    if not line.strip():
        return None
    try:
        frame = json.loads(line)
    except ValueError as exc:
        raise ProtocolError("bad_frame", f"not JSON: {exc}") from exc
    if not isinstance(frame, dict) or not isinstance(frame.get("t"), str):
        raise ProtocolError("bad_frame", "a frame is a JSON object with a string `t`")
    kind = frame["t"]
    if kind not in KINDS:
        raise ProtocolError("unknown_frame", f"unknown frame kind {kind!r}", frame.get("id"))
    for field, ok in _REQUIRED.get(kind, ()):
        if field not in frame or not ok(frame[field]):
            raise ProtocolError("bad_frame", f"`{kind}` needs a valid `{field}`", frame.get("id"))
    if kind == "hello" and "worker_proto" in frame:
        if not all(isinstance(frame.get(k), str) for k in ("digest", "version")):
            raise ProtocolError(
                "bad_frame", "a worker `hello` needs a string `digest` and `version`"
            )
    if kind == "hello" and not any(_int(frame.get(k)) for k in ("shim_proto", "worker_proto")):
        raise ProtocolError("bad_frame", "`hello` needs an integer `shim_proto` or `worker_proto`")
    return frame


def hello(*, shim_proto: int = PROTO_MAJOR, state_key: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {"t": "hello", "shim_proto": shim_proto, "minor": PROTO_MINOR}
    if state_key:
        out["state_key"] = state_key
    return out


def worker_hello(*, digest: str, version: str) -> dict[str, Any]:
    return {
        "t": "hello",
        "worker_proto": PROTO_MAJOR,
        "minor": PROTO_MINOR,
        "digest": digest,
        "version": version,
    }


def request(rid: int, msg: dict[str, Any], conn: dict[str, Any]) -> dict[str, Any]:
    return {"t": "req", "id": rid, "msg": msg, "conn": conn}


def response(rid: int, msg: dict[str, Any] | None) -> dict[str, Any]:
    return {"t": "res", "id": rid, "msg": msg}


def note(rid: int | None, msg: dict[str, Any]) -> dict[str, Any]:
    return {"t": "note", "id": rid, "msg": msg}


def drain() -> dict[str, Any]:
    return {"t": "drain"}


def drained() -> dict[str, Any]:
    return {"t": "drained"}


def bye(reason: str = "") -> dict[str, Any]:
    return {"t": "bye", "reason": reason} if reason else {"t": "bye"}


def error(code: str, message: str, *, id: Any = None, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"t": "error", "code": code, "message": message, **extra}
    if id is not None:
        out["id"] = id
    return out


def _bad_hex(text: str) -> bool:
    try:
        bytes.fromhex(text)
    except ValueError:
        return True
    return False


def negotiate(shim_hello: dict[str, Any]) -> dict[str, Any] | None:
    """Worker side: None to accept the shim's hello, else the `error` frame to answer with."""
    proto = shim_hello.get("shim_proto")
    if not _int(proto):
        return error("bad_frame", "a shim hello carries an integer `shim_proto`")
    key = shim_hello.get("state_key", "")
    if not isinstance(key, str) or _bad_hex(key):
        return error("bad_frame", "`state_key` must be a hex string")
    if proto != PROTO_MAJOR:
        return error(
            "incompatible_shim_proto",
            f"this worker speaks protocol {PROTO_MAJOR}, the shim {proto}",
            worker_proto=PROTO_MAJOR,
        )
    return None

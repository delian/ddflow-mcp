"""The ACP wire: ONE adapter for the Agent Client Protocol, both directions.

ACP is JSON-RPC over stdio between an editor (the client) and an agent. ddflow stands on
either side, so two ports are declared here next to their default adapters
(D-adapters-answers, D-acp-r2):

* `AcpAgent` -- serve ddflow AS an ACP agent: `initialize`, `session/new` (the client injects
  MCP servers, kept on the session), `session/prompt` streaming `session/update`
  notifications through the caller's `on_prompt`, and `session/cancel`.
* `AcpClient` -- drive a lane over ACP (vibe-acp, `dsh --profile acp`, ...): start the agent
  through an INJECTED `Starter` (this layer imports no `subprocess`: `infra.proc` is the one
  home of it), negotiate the version, open a session, prompt it, map the streamed updates to
  the common lane events (`AcpEvent`), answer permission requests through the caller's policy
  and cancel.

Framing, ids, `Channel`, `Starter` and `Outcome` are the MCP wire's (`.mcp`): the same
newline-delimited JSON-RPC, written once. A permission request with no policy is refused, never
allowed; silence is `timeout` (unknown), never a pass; an agent that cannot start is
`unavailable`; the protocol-version fact is three-valued (`Negotiation.supported`).

Pure standard library. The shipped stand-ins are `FakeAcpAgent` and `FakeAcpClient` in
`ddflow.adapters.fakes.acp`; the same cases run against both (tests/test_adapter_acp.py).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .mcp import (
    EOF,
    ERROR,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    OK,
    POLL_S,
    TIMEOUT,
    UNAVAILABLE,
    Channel,
    Handshake,
    Negotiation,
    Outcome,
    Starter,
    StdioServer,
    check_request,
    describe_answer,
    encode,
    err,
    ok,
    request,
    response_in,
)
from .ports import port

__all__ = [
    "AcpAgent",
    "AcpClient",
    "AcpEvent",
    "AcpHandshake",
    "AcpSession",
    "AcpStdioAgent",
    "AcpStdioClient",
    "Channel",
    "PromptResult",
    "Starter",
]

#: The ACP protocol versions this wire serves (ACP numbers them with one integer).
SERVED_VERSIONS = (1,)
#: The `stopReason` of a prompt that was cancelled or whose permission was refused.
CANCELLED = "cancelled"
#: `sessionUpdate` value -> the common lane event kind; any other value is `other`.
UPDATE_KINDS = {
    "agent_message_chunk": "message",
    "agent_thought_chunk": "thought",
    "tool_call": "tool",
    "tool_call_update": "tool_update",
    "plan": "plan",
}

Emit = Callable[[dict[str, Any]], None]
#: The agent's turn: `(session, prompt text, emit)` -> the stop reason. `emit(update)` streams a
#: `session/update`; a threaded caller reads `session.cancelled` between steps.
OnPrompt = Callable[["AcpSession", str, Emit], str]
#: The client's permission policy: the `session/request_permission` params -> the `optionId`
#: to select, or None to refuse (cancelled).
Policy = Callable[[dict[str, Any]], "str | None"]


def initialize_request(mid: Any = 1, version: int = SERVED_VERSIONS[0]) -> dict[str, Any]:
    return request(
        mid,
        "initialize",
        {"protocolVersion": version, "clientCapabilities": {}},  # v1: no fs, no terminal
    )


def negotiate_version(offered: int, answered: Any) -> Negotiation:
    """The version fact of one `initialize`: True served, False another number (a FACT), None
    no usable answer. Never False for silence. Strings carry the integers (shared type)."""
    if isinstance(answered, bool) or not isinstance(answered, int):
        return Negotiation(str(offered))
    return Negotiation(str(offered), str(answered), answered in SERVED_VERSIONS)


@dataclass
class AcpEvent:
    """One streamed update in the common lane vocabulary; `data` is the raw update."""

    kind: str
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)


def lane_event(update: dict[str, Any]) -> AcpEvent:
    content = update.get("content")
    text = content.get("text") if isinstance(content, dict) else None
    text = text or update.get("title") or ""
    return AcpEvent(UPDATE_KINDS.get(str(update.get("sessionUpdate")), "other"), str(text), update)


def prompt_text(blocks: Any) -> str:
    """The text blocks of a `session/prompt`, joined (other block kinds carry no text)."""
    if not isinstance(blocks, list):
        return ""
    return "\n".join(
        str(b.get("text", "")) for b in blocks if isinstance(b, dict) and b.get("type") == "text"
    )


@dataclass
class AcpSession(Outcome):
    """A `session/new` outcome. On the agent side it is the session state."""

    session_id: str = ""
    cwd: str = ""
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)
    cancelled: bool = False
    #: A turn is running: only then does a cancel apply (an idle cancel is a no-op).
    active: bool = False


@dataclass
class AcpHandshake(Handshake):
    @property
    def speaks_acp(self) -> bool | None:
        """True answered, False a fact (gone, wrong thing), None could not tell."""
        return self.speaks_mcp


@dataclass
class PromptResult(Outcome):
    stop_reason: str = ""
    events: list[AcpEvent] = field(default_factory=list)
    #: (tool title, optionId chosen; "" when refused) per permission request, in order.
    permissions: list[tuple[str, str]] = field(default_factory=list)


# -- the agent side ----------------------------------------------------------------------


@port
@runtime_checkable
class AcpAgent(Protocol):
    """Serve ddflow as an ACP agent over newline-delimited JSON-RPC.

    Home: `ddflow.adapters.acp` (fake: `FakeAcpAgent`). Single adapter.
    """

    def session(self, session_id: str) -> AcpSession | None:
        """The state of an open session, None when there is none by that id."""

    def handle(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        """Answer one parsed message; None for a notification (`session/cancel`)."""

    def serve(self, lines: Iterable[str]) -> None:
        """Read frames until the input ends, answering each."""


class AcpStdioAgent:
    """The default `AcpAgent`: the MCP line transport (`StdioServer`) with ACP's methods.

    `write` takes one encoded line; `on_prompt` is the agent's turn (see `OnPrompt`)."""

    def __init__(
        self,
        write: Callable[[str], None],
        *,
        on_prompt: OnPrompt,
        agent_info: dict[str, Any] | None = None,
        versions: tuple[int, ...] = SERVED_VERSIONS,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._wire = StdioServer(write, log=log)
        self._on_prompt = on_prompt
        self.agent_info = agent_info or {"name": "ddflow", "version": "0"}
        self.versions = versions
        self.sessions: dict[str, AcpSession] = {}

    def session(self, session_id: str) -> AcpSession | None:
        return self.sessions.get(session_id)

    def handle(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        method, params = msg.get("method"), msg.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        if method == "session/cancel":
            sid = params.get("sessionId")
            if isinstance(sid, str) and (session := self.sessions.get(sid)) is not None:
                session.cancelled = session.active
            if "id" in msg:  # request-shaped by mistake: a request is always answered
                return err(msg["id"], INVALID_PARAMS, "session/cancel is a notification")
            return None  # a malformed one is ignored, never raised
        if "id" not in msg:
            return None
        if (refusal := check_request(msg)) is not None:
            return refusal
        mid = msg["id"]
        if method == "initialize":
            want = params.get("protocolVersion")
            version = want if type(want) is int and want in self.versions else self.versions[0]
            caps = {"loadSession": False, "promptCapabilities": {}}
            return ok(
                mid,
                {
                    "protocolVersion": version,
                    "agentCapabilities": caps,
                    "agentInfo": self.agent_info,
                },
            )
        if method == "session/new":
            return self._new(mid, params)
        if method == "session/prompt":
            return self._prompt(mid, params)
        return err(mid, METHOD_NOT_FOUND, f"method not found: {method}")

    def _new(self, mid: Any, params: dict[str, Any]) -> dict[str, Any]:
        cwd, servers = params.get("cwd"), params.get("mcpServers", [])
        if not isinstance(cwd, str) or not isinstance(servers, list):
            return err(
                mid, INVALID_PARAMS, "session/new needs a string `cwd` and a list `mcpServers`"
            )
        sid = f"s{len(self.sessions) + 1}"
        self.sessions[sid] = AcpSession(OK, session_id=sid, cwd=cwd, mcp_servers=servers)
        return ok(mid, {"sessionId": sid})

    def _prompt(self, mid: Any, params: dict[str, Any]) -> dict[str, Any]:
        sid = params.get("sessionId")
        session = self.sessions.get(sid) if isinstance(sid, str) else None
        if session is None:
            return err(mid, INVALID_PARAMS, f"unknown session: {sid}")

        def emit(update: dict[str, Any]) -> None:
            params = {"sessionId": sid, "update": update}
            self._wire.notify({"jsonrpc": "2.0", "method": "session/update", "params": params})

        session.active, session.cancelled = True, False
        try:
            stop = self._on_prompt(session, prompt_text(params.get("prompt")), emit)
        finally:
            session.active = False
        cancelled, session.cancelled = session.cancelled, False
        return ok(mid, {"stopReason": CANCELLED if cancelled else stop})

    def serve(self, lines: Iterable[str]) -> None:
        self._wire.serve(lines, self.handle)


# -- the client side ---------------------------------------------------------------------


@port
@runtime_checkable
class AcpClient(Protocol):
    """Start an ACP agent, negotiate, open a session, prompt it and cancel, bounded in time.

    Home: `ddflow.adapters.acp` (fake: `FakeAcpClient`). Single adapter. Silence is `timeout`
    (unknown), never a pass; an agent that cannot start is `unavailable`; a permission request
    with no policy is refused.
    """

    def connect(self, timeout_s: float, *, version: int = 1) -> AcpHandshake:
        """Start the agent and `initialize`; the handshake says what happened."""

    def new_session(
        self, cwd: str, mcp_servers: list[dict[str, Any]], timeout_s: float
    ) -> AcpSession:
        """`session/new`, injecting `mcp_servers`; the id is `session_id`."""

    def prompt(
        self,
        session_id: str,
        text: str,
        timeout_s: float,
        *,
        permission: Policy | None = None,
        on_event: Callable[[AcpEvent], None] | None = None,
    ) -> PromptResult:
        """`session/prompt`: collect (and pass to `on_event`) the streamed updates, answer
        permission requests through `permission`, return the stop reason."""

    def cancel(self, session_id: str) -> bool:
        """`session/cancel` (a notification); False when it could not be sent."""

    def close(self) -> None:
        """Stop the agent; safe to call twice."""


class AcpStdioClient:
    """The default `AcpClient`, over an injected `Starter`."""

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

    def connect(self, timeout_s: float, *, version: int = SERVED_VERSIONS[0]) -> AcpHandshake:
        none = negotiate_version(version, None)
        try:
            self._channel = self._starter(self._argv, self._env)
        except OSError as exc:
            return AcpHandshake(
                UNAVAILABLE, detail=f"could not start {self._argv[:1]}: {exc}", protocol=none
            )
        self._next_id = 1
        first = self._exchange(initialize_request(1, version), timeout_s, None)
        hello = AcpHandshake(**vars(first), protocol=none)
        if first.status == OK:
            info = first.result.get("agentInfo")
            fake = {"result": {"serverInfo": info if isinstance(info, dict) else {}}}
            hello.detail, hello.server = describe_answer(fake)
            hello.protocol = negotiate_version(version, first.result.get("protocolVersion"))
        elif first.status == ERROR:
            hello.detail = describe_answer({"error": first.error})[0]
        return hello

    def new_session(
        self, cwd: str, mcp_servers: list[dict[str, Any]], timeout_s: float
    ) -> AcpSession:
        out = self._ask("session/new", {"cwd": cwd, "mcpServers": mcp_servers}, timeout_s)
        sid = out.result.get("sessionId")
        return AcpSession(
            **vars(out),
            session_id=sid if isinstance(sid, str) else "",
            cwd=cwd,
            mcp_servers=mcp_servers,
        )

    def prompt(
        self,
        session_id: str,
        text: str,
        timeout_s: float,
        *,
        permission: Policy | None = None,
        on_event: Callable[[AcpEvent], None] | None = None,
    ) -> PromptResult:
        params = {"sessionId": session_id, "prompt": [{"type": "text", "text": text}]}
        result = PromptResult(UNAVAILABLE, detail="not connected: call connect first")
        if self._channel is None:
            return result

        def seen(msg: dict[str, Any]) -> None:
            self._on_other(msg, result, permission, on_event)

        self._next_id += 1
        out = self._exchange(request(self._next_id, "session/prompt", params), timeout_s, seen)
        merged = PromptResult(**vars(out), events=result.events, permissions=result.permissions)
        merged.stop_reason = str(out.result.get("stopReason", ""))
        return merged

    def cancel(self, session_id: str) -> bool:
        frame = {"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": session_id}}
        return self._send(encode(frame))

    def close(self) -> None:
        channel, self._channel = self._channel, None
        if channel is not None:
            channel.close()

    def _send(self, line: str) -> bool:
        if self._channel is None:
            return False
        try:
            self._channel.send(line)
            return True
        except OSError:
            return False

    def _ask(self, method: str, params: dict[str, Any], timeout_s: float) -> Outcome:
        if self._channel is None:
            return Outcome(UNAVAILABLE, detail="not connected: call connect first")
        self._next_id += 1
        return self._exchange(request(self._next_id, method, params), timeout_s, None)

    def _on_other(self, msg, result, permission, on_event) -> None:
        """A frame that is not our answer: an update to map, or a request of the agent's."""
        method = msg.get("method")
        params = msg.get("params")
        params = params if isinstance(params, dict) else {}
        if method == "session/update":
            update = params.get("update")
            if isinstance(update, dict):
                event = lane_event(update)
                result.events.append(event)
                if on_event is not None:
                    on_event(event)
        elif method == "session/request_permission" and "id" in msg:
            chosen = permission(params) if permission is not None else None
            call = params.get("toolCall")
            title = str(call.get("title", "")) if isinstance(call, dict) else ""
            result.permissions.append((title, chosen or ""))
            outcome = (
                {"outcome": "selected", "optionId": chosen} if chosen else {"outcome": CANCELLED}
            )
            self._send(encode(ok(msg["id"], {"outcome": outcome})))
        elif "id" in msg and method:
            self._send(encode(err(msg["id"], METHOD_NOT_FOUND, f"method not found: {method}")))

    def _exchange(self, frame: dict[str, Any], timeout_s: float, seen) -> Outcome:
        """Send `frame`, hand every other frame to `seen`, read until its answer, the
        deadline or the end of the stream."""
        mid, t0 = frame["id"], self._clock()
        deadline = t0 + timeout_s

        def done(status: str, **kw: Any) -> Outcome:
            return Outcome(status, elapsed_s=round(self._clock() - t0, 2), **kw)

        if not self._send(encode(frame)):
            return done(EOF, detail="the agent closed before reading the request")
        while True:
            left = deadline - self._clock()
            if left <= 0:
                return done(TIMEOUT, detail=f"no answer within {timeout_s:g}s")
            try:
                line = self._channel.receive(min(left, self._poll))  # type: ignore[union-attr]
            except EOFError:
                return done(EOF, detail="the agent closed its output")
            if line is None:
                continue
            msg = response_in(line, mid)
            if msg is None:
                if seen is not None:
                    other = _json(line)
                    if other is not None:
                        seen(other)
                continue
            if "result" in msg:
                return done(OK, result=msg["result"] if isinstance(msg["result"], dict) else {})
            return done(ERROR, error=msg["error"] if isinstance(msg["error"], dict) else {})


def _json(line: str | bytes) -> dict[str, Any] | None:
    try:
        msg = json.loads(line)
    except ValueError:
        return None
    return msg if isinstance(msg, dict) else None

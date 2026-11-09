"""The agent-lane port: how ddflow starts, watches, steers and stops a coding agent.

ONE contract, one driver per TRANSPORT (D-DRV-4); the vocabulary and the registry live here
(ADP-agent-lanes), `ddflow.services.lane_driver` re-exports them and keeps the descriptor
glue (`spec_from_headless`). The default adapter, ``process-headless``
(`ddflow.services.lane_headless`, which spawns through `infra.proc`: this layer imports no
other ddflow layer and no `subprocess`), registers itself under its transport name; the other
transports (OpenCode, ACP, tmux) register the same way. The shipped stand-in is
`FakeLane` in `ddflow.adapters.fakes.lanes`, which replays a scripted transcript; the same
cases run against it and the default adapter (tests/test_adapter_lanes.py).

Rules every driver keeps: launch is an argv list, never a shell line; secrets travel by env
NAME and their values stay out of every repr, event and status; the lane gets a private home;
cleanup is one process-group kill with an optional scope; a capability the transport does not
have is reported ``unavailable`` and never imitated. See docs/ddflow/lane-driver.md.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .ports import port
from .registry import Registry

#: The capability vocabulary; a driver advertises a subset, a caller asks before it calls.
CAPABILITIES = frozenset(
    {"steer", "interrupt", "resume", "usage", "tool_events", "permission_broker"}
)
#: Lane states. ``exited`` = ended by itself with code 0; ``failed`` = nonzero or never ran;
#: ``stopped`` = ended by ``stop``.
STATES = ("starting", "running", "exited", "failed", "stopped")
RESULT_STATUSES = ("ok", "unavailable", "failed")
#: Transports that ship in the tree: module names imported on first use, each registering itself.
BUILTIN_TRANSPORTS = {"process-headless": "ddflow.services.lane_headless"}


@dataclass(frozen=True)
class Result:
    """What ``steer`` and ``stop`` return: ok, unavailable (the transport cannot) or failed."""

    status: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def ok(detail: str = "") -> Result:
    return Result("ok", detail)


def unavailable(what: str) -> Result:
    return Result("unavailable", what)


def failed(what: str) -> Result:
    return Result("failed", what)


@dataclass(frozen=True)
class LaunchSpec:
    """Everything needed to start one lane. Build it from a descriptor with ``spec_from_headless``."""

    name: str  #: lane identity; names the private home and the scope unit
    argv: tuple[str, ...]  #: executable + args; whole-element placeholders (engines.render_argv)
    values: Mapping[str, str] = field(default_factory=dict)  #: placeholder -> value
    cwd: str | None = None  #: the lane worktree
    prompt: str = ""  #: written to a private file for {prompt_file}, else sent on stdin
    env: Mapping[str, str] = field(default_factory=dict)  #: child var -> SOURCE env NAME (keys)
    secret_env: Mapping[str, str] = field(
        default_factory=dict, repr=False
    )  #: child var -> secret VALUE (a key resolved at prepare, e.g. a lane endpoint key read
    #: from its variable or key file); the value reaches only the child env, never `repr`,
    #: `describe` or the process environment
    fixed_env: Mapping[str, str] = field(
        default_factory=dict
    )  #: literal, non-secret (telemetry off)
    home_env: str = ""  #: the agent's own home variable (CODEX_HOME, DSH_HOME), if it has one
    home: Path | None = None  #: private home; None = a fresh temporary one, removed at cleanup
    pass_env: tuple[str, ...] = ()  #: names inherited from the launcher ([lanes].pass_env)
    stream_rules: tuple[Any, ...] = ()  #: E5 `stream_map.Rule`s; empty = no events
    limit: Any = None  #: E6 `limit_detect.Spec`, or None
    exit_codes: Mapping[str, str] = field(default_factory=dict)  #: code -> meaning
    resume: bool = False  #: the transport can start with a session id (resume flag present)
    scope: str = "none"  #: none | systemd ([lanes].scope)
    stop_grace_s: float = 10.0
    event_buffer: int = 1000


@dataclass(frozen=True)
class LaneEvent:
    """A canonical event of one lane: ``event`` is one of ``stream_map.EVENTS``."""

    seq: int
    lane: str
    event: str
    fields: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LaneStatus:
    state: str
    pid: int | None = None
    exit_code: int | None = None
    exit_meaning: str = ""  #: from the descriptor's exit-code table; "" = not listed
    limit: str = ""  #: how a usage limit was detected (exit_code|stream_event|message), or ""
    reset: str = ""  #: reset text a limit message gave
    session: str = ""  #: the agent's own session id, from its `started` event
    events: int = 0
    skipped: int = 0  #: stream lines that gave no event
    dropped: int = 0  #: events lost to a full buffer
    capabilities: frozenset[str] = frozenset()
    detail: str = ""


@runtime_checkable
class LaneHandle(Protocol):
    id: str
    transport: str
    capabilities: frozenset[str]


class Capable(Protocol):
    """What `advertise` needs of a lane: the capabilities it has."""

    capabilities: frozenset[str]


@port
@runtime_checkable
class Lane(Protocol):
    """One implementation per transport, registered by name in `TRANSPORTS`; the default is
    ``process-headless`` (`ddflow.services.lane_headless`, home planned in ddflow.adapters.lanes).
    A lane object also carries ``capabilities`` (the most the transport can do, see `Capable`:
    a data member cannot sit in a Protocol that is checked with issubclass); a handle's own set
    (``LaneHandle.capabilities``) is what this lane can."""

    def start(self, spec: LaunchSpec) -> LaneHandle | Result:
        """Launch. A Result (unavailable or failed) when it cannot; never raises for that."""

    def events(self, h: LaneHandle, idle_s: float | None = None) -> Iterator[LaneEvent]:
        """Canonical events in order; ends when the lane's stream ends or after ``idle_s`` quiet."""

    def steer(self, h: LaneHandle, text: str) -> Result:
        """Send a message to a running lane; unavailable if ``steer`` is not a capability."""

    def stop(self, h: LaneHandle, grace_s: float | None = None) -> Result:
        """Ask, wait ``grace_s``, then kill the whole lane; remove its private files."""

    def status(self, h: LaneHandle) -> LaneStatus: ...


#: transport name -> factory; a different factory for a taken name is refused (a `ValueError`).
TRANSPORTS: Registry[Callable[[], Lane]] = Registry("lane transport", same_ok=True)


def register_transport(name: str, factory: Callable[[], Lane]) -> None:
    """Add a transport (idempotent for the same factory; a different one for a name is refused)."""
    TRANSPORTS.register(factory, name)


def transports() -> tuple[str, ...]:
    """Registered plus built-in transport names."""
    return tuple(sorted(set(TRANSPORTS.table) | set(BUILTIN_TRANSPORTS)))


def driver_for(transport: str) -> Lane | Result:
    """The driver for ``transport``, or an ``unavailable`` Result naming what is known."""
    if transport not in TRANSPORTS.table and transport in BUILTIN_TRANSPORTS:
        try:
            importlib.import_module(BUILTIN_TRANSPORTS[transport])
        except ImportError as e:
            return unavailable(f"lane transport {transport!r} cannot be loaded: {e}")
    factory = TRANSPORTS.table.get(transport)
    if factory is None:
        return unavailable(f"no lane transport {transport!r}; known: {list(transports())}")
    return factory()


def advertise(driver: object, wanted: set[str] | frozenset[str]) -> frozenset[str]:
    """``wanted`` limited to what the driver can do, and to the known vocabulary."""
    return frozenset(wanted) & CAPABILITIES & getattr(driver, "capabilities", frozenset())

"""The Lane contract: how ddflow starts, watches, steers and stops a coding agent.

One small interface, one driver per TRANSPORT (research driver-architecture 4.4, D-DRV-4).
``process-headless`` is built (``lane_headless``); ACP, Pi RPC, OpenCode HTTP and tmux plug in
later through ``register_transport`` behind the same five calls plus a capability set.
This module holds only the vocabulary and the registry, so a transport imports it and nothing
else of ddflow's lane code.

Rules every driver keeps: launch is an argv list, never a shell line; secrets travel by env
NAME and their values stay out of every repr, event and status; the lane gets a private home;
cleanup is one process-group kill with an optional scope; a capability the transport does not
have is reported ``unavailable`` and never imitated. See docs/ddflow/lane-driver.md.
"""

from __future__ import annotations

import importlib
import shlex
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .engines import limit_detect, stream_map
from .harnessv2 import Autonomy, Headless

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
    fixed_env: Mapping[str, str] = field(
        default_factory=dict
    )  #: literal, non-secret (telemetry off)
    home_env: str = ""  #: the agent's own home variable (CODEX_HOME, DSH_HOME), if it has one
    home: Path | None = None  #: private home; None = a fresh temporary one, removed at cleanup
    pass_env: tuple[str, ...] = ()  #: names inherited from the launcher ([lanes].pass_env)
    stream_rules: tuple[stream_map.Rule, ...] = ()  #: E5; empty = the lane reports no events
    limit: limit_detect.Spec | None = None  #: E6
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


@runtime_checkable
class Lane(Protocol):
    """One implementation per transport. ``capabilities`` is the most the transport can do;
    a handle's own set (``LaneHandle.capabilities``) is what this lane can."""

    capabilities: frozenset[str]

    def start(self, spec: LaunchSpec) -> LaneHandle | Result:
        """Launch. A Result (unavailable or failed) when it cannot; never raises for that."""

    def events(self, h: LaneHandle, idle_s: float | None = None) -> Iterator[LaneEvent]:
        """Canonical events in order; ends when the lane's stream ends or after ``idle_s`` quiet."""

    def steer(self, h: LaneHandle, text: str) -> Result:
        """Send a message to a running lane; unavailable if ``steer`` is not a capability."""

    def stop(self, h: LaneHandle, grace_s: float | None = None) -> Result:
        """Ask, wait ``grace_s``, then kill the whole lane; remove its private files."""

    def status(self, h: LaneHandle) -> LaneStatus: ...


_TRANSPORTS: dict[str, Callable[[], Lane]] = {}


def register_transport(name: str, factory: Callable[[], Lane]) -> None:
    """Add a transport (idempotent for the same factory; a different one for a name is refused)."""
    if name in _TRANSPORTS and _TRANSPORTS[name] is not factory:
        raise ValueError(f"lane transport {name!r} is already registered")
    _TRANSPORTS[name] = factory


def transports() -> tuple[str, ...]:
    """Registered plus built-in transport names."""
    return tuple(sorted(set(_TRANSPORTS) | set(BUILTIN_TRANSPORTS)))


def driver_for(transport: str) -> Lane | Result:
    """The driver for ``transport``, or an ``unavailable`` Result naming what is known."""
    if transport not in _TRANSPORTS and transport in BUILTIN_TRANSPORTS:
        importlib.import_module(BUILTIN_TRANSPORTS[transport])
    factory = _TRANSPORTS.get(transport)
    if factory is None:
        return unavailable(f"no lane transport {transport!r}; known: {list(transports())}")
    return factory()


def advertise(driver: Lane, wanted: set[str] | frozenset[str]) -> frozenset[str]:
    """``wanted`` limited to what the driver can do, and to the known vocabulary."""
    return frozenset(wanted) & CAPABILITIES & driver.capabilities


def spec_from_headless(
    name: str,
    headless: Headless,
    *,
    autonomy: Autonomy | None = None,
    stream_rules: tuple[stream_map.Rule, ...] = (),
    limit: limit_detect.Spec | None = None,
    model: str = "",
    effort: str = "",
    max_turns: int = 0,
    mcp_config: str = "",
    session: str = "",
    **spec: Any,
) -> LaunchSpec | Result:
    """A ``LaunchSpec`` from a descriptor's ``[headless]`` section.

    A flag the descriptor names is added with its ``{placeholder}`` value only when the template
    does not already carry that placeholder and the caller gave a value. Missing pieces make an
    ``unavailable`` Result (no argv, ``none = true``, a resume requested without a resume flag,
    an autonomy profile that does not exist), never a guess."""
    if headless.none or not headless.argv:
        return unavailable("the descriptor has no [headless] launch template")
    argv = list(headless.argv)
    values: dict[str, str] = {"dir": spec.get("cwd") or ""}
    for flag, ph, val in (
        (headless.model_flag, "model", model),
        (headless.effort_flag, "effort", effort),
        (headless.max_turns_flag, "max_turns", max_turns or ""),
        (headless.mcp_flag, "mcp_config", mcp_config),
        (headless.resume_flag, "session", session),
    ):
        if val != "" and not flag:
            return unavailable(f"the descriptor has no flag for {ph}; it is not guessed")
        if val != "":
            values[ph] = str(val)
            if "{" + ph + "}" not in argv:
                argv += [flag, "{" + ph + "}"]
    if headless.autonomy:
        profile = (autonomy.profiles if autonomy and not autonomy.none else {}).get(
            headless.autonomy
        )
        if profile is None:
            return unavailable(f"autonomy profile {headless.autonomy!r} is not in [autonomy]")
        argv += shlex.split(profile)  # static descriptor data split into args, no shell
    if headless.output_flag:
        argv += [
            headless.output_flag,
            *([headless.output_format] if headless.output_format else []),
        ]
    return LaunchSpec(
        name=name,
        argv=tuple(argv),
        values={k: v for k, v in values.items() if v != ""},
        stream_rules=stream_rules,
        limit=limit,
        exit_codes=dict(headless.exit_codes),
        resume=bool(headless.resume_flag),
        **spec,
    )


__all__ = [
    "BUILTIN_TRANSPORTS",
    "CAPABILITIES",
    "STATES",
    "Lane",
    "LaneEvent",
    "LaneHandle",
    "LaneStatus",
    "LaunchSpec",
    "Result",
    "advertise",
    "driver_for",
    "failed",
    "ok",
    "register_transport",
    "spec_from_headless",
    "transports",
    "unavailable",
]

"""Descriptor v2: the sections agent parity needs, and the names reserved for later tasks.

Same three-valued rule as the rest of the descriptor (see ``harnessreg``): a section left
out is NOT VERIFIED, ``none = true`` is a documented absence, values are the fact. Every
section of the schema is reserved HERE, once; later tasks only fill values. This module
imports no other harness module, so ``harnessreg`` can import it freely.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class DescriptorError(ValueError):
    """A descriptor that is malformed; the message names the file and the field."""


#: How an agent is kept working after it would stop.
CONTINUATION_MECHANISMS = frozenset(
    {
        "stop-hook-block",
        "followup-message",
        "plugin-idle-prompt",
        "plugin-agent-end",
        "external-driver",
        "none",
    }
)

#: The loop primitive a harness runs the orchestration wave loop under. Every value is a
#: BACKGROUND primitive on purpose: the wave orchestration must never block the operator's
#: main chat with a foreground loop, so no "goal" or "foreground" value exists to pick, and
#: disabling the section is the record's ``none = true`` field, never a loop value.
ORCHESTRATION_LOOPS = frozenset(
    {
        "background-subagents",  # background task subagents (Kilo's `task` tool)
        "self-paced-command",  # a self-paced slash command (Claude Code's /loop)
        "subtask",  # a background subtask agent (opencode)
        "second-window",  # a second window / terminal process (VS Code)
    }
)

#: Loop primitives whose lane CAN complete (return a final result and end its turn). A
#: resume a completing lane schedules for after that turn is dropped by the harness at
#: completion, so such a loop MUST name an ``orchestration.keepalive`` -- how the lane keeps
#: its own session alive across waits -- instead of relying on a self-scheduled wakeup
#: (Bbc611c0165). ``self-paced-command`` runs until it stops itself, so it is its own keepalive.
BACKGROUND_LOOPS = ("background-subagents", "subtask", "second-window")

#: How a lane keeps its OWN session alive across the wave loop's waits, so it never
#: completes prematurely and its claim lease cannot lapse while work or a wait is in flight.
ORCHESTRATION_KEEPALIVES = frozenset(
    {
        "session-goal",  # the lane wraps the wave loop in its own session goal (alive across turns)
        "self-paced-command",  # a self-paced slash command that runs until it stops itself
        "second-window",  # the second window / terminal the loop runs in, alive until the scope drains
        "in-process-wait",  # the lane blocks in a live `ddflow wait` process until the scope drains
        "persistent-process",  # a persistent background process (inherit/persistent lifetime)
    }
)


@dataclass(frozen=True)
class Detect:
    """How a running agent is recognised."""

    none: bool = False
    client_names: tuple[str, ...] = ()  #: MCP clientInfo.name values
    env: tuple[str, ...] = ()  #: environment variables that only this agent sets
    process_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class Headless:
    """Running the agent unattended. Permission flags live in [autonomy], referenced by id."""

    none: bool = False
    argv: tuple[str, ...] = ()  #: command template; placeholders are the driver's
    model_flag: str = ""
    effort_flag: str = ""
    mcp_flag: str = ""  #: how MCP servers are injected at launch
    autonomy: str = ""  #: a profile id of [autonomy].profiles
    max_turns_flag: str = ""
    output_flag: str = ""
    output_format: str = ""
    resume_flag: str = ""
    exit_codes: dict[str, str] = field(default_factory=dict)  #: code -> meaning


@dataclass(frozen=True)
class Continuation:
    none: bool = False
    mechanism: str = ""  #: one of CONTINUATION_MECHANISMS
    cap: int = 0  #: the most continuations the mechanism allows
    goal_command: str = ""
    loop_guard: str = ""


@dataclass(frozen=True)
class Orchestration:
    """How the harness runs the ddflow wave loop, as data.

    The loop is harness-agnostic: read ``ddflow next``'s ready set, CLAIM each ready item,
    fan each claimed item to a subagent by its ``tier`` hint, re-read ``next`` after each
    merge or park, wait in the background (never a foreground poll), persist the wave
    position, and stop only on an empty scope (no ready, held, blocked or in-flight items)
    or a question/operator gate. This record names a harness's primitives for those steps;
    its driver delta and command/agent files are the emitted prose instances.
    """

    none: bool = False
    loop: str = ""  #: one of ORCHESTRATION_LOOPS — always a BACKGROUND primitive
    dispatch: str = ""  #: the subagent fan-out primitive (one claimed item per ready id)
    wait: str = ""  #: the wait primitive (background process + `ddflow wait`)
    keepalive: str = ""  #: how the lane stays alive across waits (one of ORCHESTRATION_KEEPALIVES)
    persist: str = ""  #: where the wave position persists and resumes
    stop: str = ""  #: the ONLY stops: empty scope, or the question/ask/approval operator gates


@dataclass(frozen=True)
class Meters:
    none: bool = False
    context: str = ""  #: where context use is read from
    quota: str = ""  #: where remaining quota is read from


@dataclass(frozen=True)
class Caps:
    none: bool = False
    mcp_tools: int = 0
    instructions_bytes: int = 0
    subagent_depth: int = 0


@dataclass(frozen=True)
class Autonomy:
    """Unattended permission settings: profile id -> flags, and the files that hold them."""

    none: bool = False
    profiles: dict[str, str] = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)  #: repo path -> what it holds


@dataclass(frozen=True)
class Telemetry:
    none: bool = False
    transcript_path: str = ""
    transcript_format: str = ""
    otel: str = ""


@dataclass(frozen=True)
class Reserved:
    """A section whose name is reserved; its owner task defines the keys. Until then it
    holds ``none`` or free string values."""

    none: bool = False
    values: dict[str, str] = field(default_factory=dict)


#: Typed v2 sections, parsed with the strict loader.
V2_SECTIONS: dict[str, type] = {
    "detect": Detect,
    "headless": Headless,
    "continuation": Continuation,
    "orchestration": Orchestration,
    "meters": Meters,
    "caps": Caps,
    "autonomy": Autonomy,
    "telemetry": Telemetry,
}

#: Reserved names, one definition each; the owner fills them (see the task's note).
RESERVED_SECTIONS = (
    "research",
    "plan",
    "bot",
    "adapters",
    "spawn",
    "doors",
    "acp",
    "platform",
    "governor",
    "test",
)


def parse_reserved(table: Any, where: str) -> Reserved:
    """``none = true`` or string values only; anything else names the key."""
    if not isinstance(table, dict):
        raise DescriptorError(f"{where}: expected a table")
    none = table.get("none", False)
    if type(none) is not bool:
        raise DescriptorError(f"{where}.none: expected bool, got {type(none).__name__}")
    values = {k: v for k, v in table.items() if k != "none"}
    for k, v in values.items():
        if not isinstance(v, str):
            raise DescriptorError(f"{where}.{k}: expected str, got {type(v).__name__}")
    return Reserved(none=none, values=values)


def check_table(table: Any, name: str, where: str) -> None:
    """Checks on the RAW table of a v2 section, where "was a value given" is known exactly
    (a falsy value such as ``subagent_depth = 0`` is still a fact)."""
    if not isinstance(table, dict):
        return  # _build names the wrong type
    keys = set(table) - {"none"}
    if not table:
        raise DescriptorError(f"{where}: [{name}] is empty: omit it or say none = true")
    if not keys and table.get("none") is not True:
        raise DescriptorError(f"{where}: [{name}] gives no values: omit it or say none = true")
    _membership(
        name, table.get("mechanism"), "continuation.mechanism", CONTINUATION_MECHANISMS, where
    )
    _membership(name, table.get("loop"), "orchestration.loop", ORCHESTRATION_LOOPS, where)
    _membership(
        name, table.get("keepalive"), "orchestration.keepalive", ORCHESTRATION_KEEPALIVES, where
    )
    if name == "orchestration" and keys and table.get("loop") is None:
        raise DescriptorError(f"{where}: orchestration.loop names the wave loop primitive")
    if (
        name == "orchestration"
        and table.get("loop") in BACKGROUND_LOOPS
        and not table.get("keepalive")
    ):
        raise DescriptorError(
            f"{where}: orchestration.keepalive names how its {table.get('loop')!r} loop "
            "keeps the lane's own session alive across waits; without one the lane can "
            "complete and its self-scheduled resume is dropped, lapsed its claim (Bbc611c0165)"
        )
    if name == "headless" and table.get("autonomy") == "":
        raise DescriptorError(f"{where}: headless.autonomy must name an [autonomy].profiles id")
    if "none" in table and table["none"] is True and keys:
        raise DescriptorError(f"{where}: [{name}] says none = true and also gives values")
    if name == "continuation" and keys & {"cap", "goal_command", "loop_guard"}:
        if table.get("mechanism", "none") in ("none", ""):
            raise DescriptorError(
                f"{where}: continuation.cap, goal_command and loop_guard need a mechanism"
                " other than none"
            )


def _membership(name: str, value: Any, field: str, vocab: frozenset, where: str) -> None:
    """Reject a value that is not in ``vocab``, for ``field`` when this section is ``name``.

    ``None`` is "absent" and is skipped by its caller's own needs; a non-string truthy
    value (``1``, ``true``) is still not a member and fails here, exactly as the
    built-record check did before the helper extracted it.
    """
    if name != field.partition(".")[0] or value is None:
        return
    if not isinstance(value, str) or value not in vocab:
        raise DescriptorError(f"{where}: {field} must be one of {sorted(vocab)}")


def validate_reserved(reserved: dict[str, Reserved], where: str) -> None:
    for name, sec in reserved.items():
        if sec.none == bool(sec.values):  # both set, or neither
            raise DescriptorError(f"{where}: [{name}] needs exactly one of none = true or values")


def validate(sections: dict[str, Any], where: str) -> None:
    """Cross-field checks for the v2 sections present in ``sections`` (name -> record)."""
    for name, sec in sections.items():
        for k, v in vars(sec).items():
            if type(v) is int and v < 0:
                raise DescriptorError(f"{where}: [{name}].{k} must not be negative")
    cont = sections.get("continuation")
    if cont and (cont.cap or cont.goal_command or cont.loop_guard):
        if cont.mechanism in ("", "none"):
            raise DescriptorError(
                f"{where}: continuation.cap, goal_command and loop_guard need a mechanism"
                " other than none"
            )
    if cont and cont.mechanism:
        _membership(
            "continuation", cont.mechanism, "continuation.mechanism", CONTINUATION_MECHANISMS, where
        )
    head = sections.get("headless")
    if head and head.autonomy:
        auto = sections.get("autonomy")
        if auto is None or auto.none or head.autonomy not in auto.profiles:
            raise DescriptorError(
                f"{where}: headless.autonomy {head.autonomy!r} is not an [autonomy].profiles id"
            )
    orch = sections.get("orchestration")
    if orch and orch.loop:
        _membership("orchestration", orch.loop, "orchestration.loop", ORCHESTRATION_LOOPS, where)

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
    mech = table.get("mechanism")
    if name == "continuation" and isinstance(mech, str) and mech not in CONTINUATION_MECHANISMS:
        raise DescriptorError(
            f"{where}: continuation.mechanism must be one of {sorted(CONTINUATION_MECHANISMS)}"
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
    if cont and cont.mechanism and cont.mechanism not in CONTINUATION_MECHANISMS:
        raise DescriptorError(
            f"{where}: continuation.mechanism must be one of {sorted(CONTINUATION_MECHANISMS)}"
        )
    head = sections.get("headless")
    if head and head.autonomy:
        auto = sections.get("autonomy")
        if auto is None or auto.none or head.autonomy not in auto.profiles:
            raise DescriptorError(
                f"{where}: headless.autonomy {head.autonomy!r} is not an [autonomy].profiles id"
            )

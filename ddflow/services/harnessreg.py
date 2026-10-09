"""The harness registry: one TOML descriptor per coding agent, loaded into frozen records.

Everything ddflow knows about an agent used to be spread over parallel tables in
``adopt`` (its MCP file, its native rules surface, its slash-command files) and over
agent-name branches elsewhere. A descriptor in ``ddflow/harnesses/<id>.toml`` is the one
place that fact lives; ``adopt.AGENT_TARGETS``, ``NATIVE_RULES`` and ``AGENT_COMMANDS``
are views over it.

Three-valued, on purpose. Each optional section means one of:

* **absent**   -- NOT VERIFIED. Nobody has read a primary source for this fact. The
  registry returns ``None`` and a consumer must not guess.
* ``none = true`` -- documented as NOT PROVIDED by the agent (a verified absence).
* present with values -- the fact, with ``[verified]`` naming its sources and date.

Collapsing the first two is the failure this layout exists to prevent: "we do not know"
read as "it does not have one" silently drops an agent from a feature it supports.

The loader is strict. An unknown key, a wrong type, an id that differs from its file
name, a duplicate rank or an MCP shape ddflow cannot write is an error at load time,
because a typo in a descriptor is otherwise valid TOML that is silently ignored.

``hooks.normalizer`` and ``hooks.emitter`` are NAMES resolved by the hook core
(B-hx-hook-core); the registry only checks they are present when the hook style is a
command-hook style.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, get_type_hints

from .mcpconfig import (
    SHAPE_COPILOT,
    SHAPE_MCP_DOT_SERVERS,
    SHAPE_MCP_SERVERS,
    SHAPE_NONE,
    SHAPE_OPENCODE,
    SHAPE_SERVERS,
    SHAPE_TOML,
)

#: The directory of descriptors, shipped inside the package.
HARNESS_DIR = Path(__file__).resolve().parent.parent / "harnesses"

#: The lifecycle every agent's native events are mapped onto, handled once by the hook core.
CANONICAL_EVENTS = (
    "session_start",
    "prompt",
    "pre_tool",
    "post_tool",
    "pre_compact",
    "stop",
    "session_end",
)

#: Hook styles: how an agent's hooks are configured and called.
HOOK_STYLES = frozenset(
    {"claude", "gemini", "copilot", "cursor", "cline", "cascade", "antigravity", "plugin", "none"}
)
#: Styles where ddflow runs as a command hook and needs a normalizer and an emitter.
COMMAND_HOOK_STYLES = HOOK_STYLES - {"plugin", "none"}

BLOCK_MODES = frozenset({"exit2", "json", "throw", "cancel", "none"})
#: Tri-state answers for "does it read X", with `nv` = not verified.
SUPPORT = frozenset({"yes", "no", "conditional", "config", "fallback", "nv"})
NATIVE_FORMS = frozenset({"whole", "block", "aider-conf"})
SHAPES = frozenset(
    {
        SHAPE_MCP_SERVERS,
        SHAPE_SERVERS,
        SHAPE_COPILOT,
        SHAPE_MCP_DOT_SERVERS,
        SHAPE_OPENCODE,
        SHAPE_TOML,
        SHAPE_NONE,
    }
)


class DescriptorError(ValueError):
    """A descriptor that is malformed; the message names the file and the field."""


@dataclass(frozen=True)
class Mcp:
    path: str  #: project MCP config relative to the repo root; "" when none exists
    shape: str  #: one of the mcpconfig SHAPE_* constants
    note: str = ""  #: why this path and shape (what a probe or the docs showed)


@dataclass(frozen=True)
class Instructions:
    reads: tuple[str, ...] = ()  #: instruction files the agent reads
    agents_md: str = "nv"  #: SUPPORT value: does it read AGENTS.md
    claude_md: str = "nv"  #: SUPPORT value: does it read CLAUDE.md
    native_rank: int = 0  #: position among the native surfaces (listings keep a fixed order)
    native_path: str = (
        ""  #: the agent's own surface ddflow must also write; "" when AGENTS.md suffices
    )
    native_form: str = ""  #: one of NATIVE_FORMS
    native_why: str = ""  #: why AGENTS.md alone does not reach this agent


@dataclass(frozen=True)
class Hooks:
    style: str
    file: str = ""  #: where the hook config lives, relative to the repo (or `~/`)
    normalizer: str = ""  #: name of the stdin normalizer (resolved by the hook core)
    emitter: str = ""  #: name of the stdout emitter (resolved by the hook core)
    inject: tuple[str, ...] = ()  #: canonical events at which a hook's output reaches the model
    block: str = "none"  #: how a hook blocks: one of BLOCK_MODES
    session_id: str = ""  #: stdin field that carries the session id
    events: dict[str, str] = field(default_factory=dict)  #: canonical event -> native name


@dataclass(frozen=True)
class Plugin:
    none: bool = False
    mechanism: str = ""
    manifest: str = ""


@dataclass(frozen=True)
class Commands:
    none: bool = False
    dir: str = ""
    format: str = ""
    files: dict[str, str] = field(default_factory=dict)  #: repo path -> source under templates/


@dataclass(frozen=True)
class Skills:
    none: bool = False
    dir: str = ""


@dataclass(frozen=True)
class Subagents:
    none: bool = False
    dir: str = ""


@dataclass(frozen=True)
class Env:
    session_id: tuple[str, ...] = ()  #: environment variables that carry the session id


@dataclass(frozen=True)
class Worktrees:
    none: bool = False
    dir: str = ""


@dataclass(frozen=True)
class Verified:
    date: str
    sources: tuple[str, ...]
    record: str = ""  #: the research record this was read from
    probe: str = ""  #: the command run against a real binary, when one was on PATH
    unverified: tuple[str, ...] = ()  #: facts still NOT VERIFIED


@dataclass(frozen=True)
class Harness:
    """One agent. Optional sections are ``None`` when NOT VERIFIED (see the module doc)."""

    id: str
    rank: int  #: position in the listings; a descriptor file has no order of its own
    family: str  #: the lineage whose config conventions it shares
    delta: str  #: filename under `templates/drivers/deltas/`
    mcp: Mcp
    verified: Verified
    instructions: Instructions | None = None
    hooks: Hooks | None = None
    plugin: Plugin | None = None
    commands: Commands | None = None
    skills: Skills | None = None
    subagents: Subagents | None = None
    env: Env | None = None
    worktrees: Worktrees | None = None


def _check_type(value: Any, hint: Any, where: str) -> Any:
    origin = getattr(hint, "__origin__", None)
    if origin is tuple:
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise DescriptorError(f"{where}: expected a list of strings")
        return tuple(value)
    if origin is dict:
        if not isinstance(value, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in value.items()
        ):
            raise DescriptorError(f"{where}: expected a table of strings")
        return dict(value)
    if hint is bool or hint is int or hint is str:
        # bool is an int in Python; a TOML `1` for a flag is a typo, not a flag.
        if type(value) is not hint:
            raise DescriptorError(f"{where}: expected {hint.__name__}, got {type(value).__name__}")
        return value
    raise DescriptorError(f"{where}: unsupported field type {hint!r}")  # pragma: no cover


def _build(cls: type, table: Any, where: str) -> Any:
    if not isinstance(table, dict):
        raise DescriptorError(f"{where}: expected a table")
    hints = get_type_hints(cls)
    names = {f.name for f in fields(cls)}
    unknown = sorted(set(table) - names)
    if unknown:
        raise DescriptorError(f"{where}: unknown key(s) {', '.join(unknown)}")
    kwargs = {k: _check_type(v, hints[k], f"{where}.{k}") for k, v in table.items()}
    try:
        return cls(**kwargs)
    except TypeError as exc:  # a required field is missing
        raise DescriptorError(f"{where}: {exc}") from exc


_SECTIONS: dict[str, type] = {
    "instructions": Instructions,
    "hooks": Hooks,
    "plugin": Plugin,
    "commands": Commands,
    "skills": Skills,
    "subagents": Subagents,
    "env": Env,
    "worktrees": Worktrees,
}


def _validate_instructions(i: Instructions, where: str) -> None:
    for k in ("agents_md", "claude_md"):
        if getattr(i, k) not in SUPPORT:
            raise DescriptorError(f"{where}: instructions.{k} must be one of {sorted(SUPPORT)}")
    if bool(i.native_path) != bool(i.native_form) or bool(i.native_path) != bool(i.native_why):
        raise DescriptorError(
            f"{where}: instructions.native_path, native_form and native_why go together"
        )
    if i.native_form and i.native_form not in NATIVE_FORMS:
        raise DescriptorError(f"{where}: instructions.native_form must be {sorted(NATIVE_FORMS)}")


def _validate_hooks(hk: Hooks, where: str) -> None:
    if hk.style not in HOOK_STYLES:
        raise DescriptorError(f"{where}: hooks.style must be one of {sorted(HOOK_STYLES)}")
    if hk.block not in BLOCK_MODES:
        raise DescriptorError(f"{where}: hooks.block must be one of {sorted(BLOCK_MODES)}")
    bad = sorted(set(hk.events) - set(CANONICAL_EVENTS))
    if bad:
        raise DescriptorError(f"{where}: hooks.events has non-canonical key(s) {', '.join(bad)}")
    bad = sorted(set(hk.inject) - set(hk.events))
    if bad:
        raise DescriptorError(f"{where}: hooks.inject names event(s) with no mapping: {bad}")
    if hk.style == "none" and (hk.events or hk.inject or hk.file):
        raise DescriptorError(f"{where}: hooks.style 'none' takes no file, events or inject")
    if hk.style in COMMAND_HOOK_STYLES and not (hk.normalizer and hk.emitter and hk.file):
        raise DescriptorError(f"{where}: a {hk.style!r} hook style needs file, normalizer, emitter")


def _validate(h: Harness, where: str) -> None:
    if h.mcp.shape not in SHAPES:
        raise DescriptorError(f"{where}: mcp.shape {h.mcp.shape!r} is not one of {sorted(SHAPES)}")
    if (h.mcp.shape == SHAPE_NONE) != (h.mcp.path == ""):
        raise DescriptorError(f"{where}: mcp.path is empty exactly when mcp.shape is 'none'")
    if not h.verified.sources:
        raise DescriptorError(f"{where}: [verified] must name at least one source")
    if h.instructions is not None:
        _validate_instructions(h.instructions, where)
    if h.hooks is not None:
        _validate_hooks(h.hooks, where)
    for name in ("plugin", "commands", "skills", "subagents", "worktrees"):
        sec = getattr(h, name)
        if sec is not None and sec.none and any(v for k, v in vars(sec).items() if k != "none"):
            raise DescriptorError(f"{where}: [{name}] says none = true and also gives values")


def parse(text: str, name: str) -> Harness:
    """Parse one descriptor's TOML; ``name`` is the file stem the id must equal."""
    where = f"{name}.toml"
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise DescriptorError(f"{where}: {exc}") from exc
    top = {k: raw.pop(k) for k in ("id", "rank", "family", "delta") if k in raw}
    mcp = _build(Mcp, raw.pop("mcp", None), f"{where}[mcp]")
    verified = _build(Verified, raw.pop("verified", None), f"{where}[verified]")
    sections = {}
    for key, cls in _SECTIONS.items():
        if key in raw:
            sections[key] = _build(cls, raw.pop(key), f"{where}[{key}]")
    if raw:
        raise DescriptorError(f"{where}: unknown top-level key(s) {', '.join(sorted(raw))}")
    for key, want in (("id", str), ("rank", int), ("family", str), ("delta", str)):
        if key not in top:
            raise DescriptorError(f"{where}: missing top-level key {key!r}")
        _check_type(top[key], want, f"{where}.{key}")
    h = Harness(**top, mcp=mcp, verified=verified, **sections)
    if h.id != name:
        raise DescriptorError(f"{where}: id {h.id!r} must equal the file name {name!r}")
    _validate(h, where)
    return h


_cache: dict[Path, tuple[Harness, ...]] = {}


def load(directory: Path | None = None) -> tuple[Harness, ...]:
    """Every descriptor under ``directory`` (default: the shipped set), ordered by rank."""
    root = directory or HARNESS_DIR
    if root not in _cache:
        found = [parse(p.read_text(encoding="utf-8"), p.stem) for p in sorted(root.glob("*.toml"))]
        ranks: dict[int, str] = {}
        for h in found:
            if h.rank in ranks:
                raise DescriptorError(f"{h.id}.toml: rank {h.rank} is also {ranks[h.rank]}'s")
            ranks[h.rank] = h.id
        _cache[root] = tuple(sorted(found, key=lambda h: h.rank))
    return _cache[root]


def get(agent: str) -> Harness | None:
    """The descriptor for ``agent``, or ``None`` when ddflow has none."""
    return next((h for h in load() if h.id == agent), None)


def ids() -> list[str]:
    return [h.id for h in load()]

"""The declarative command registry: one description of a command, both surfaces generated.

A `Command` names an operation once -- its CLI path, its MCP tool, its parameters, the
function that runs it and the shape both surfaces print -- and this module generates what
used to be written twice by hand: the argparse subparser (`add_commands`), the MCP tool's
JSON Schema (`input_schema`) and the tool-table entry the MCP engine dispatches on
(`Command.tool_entry`). D-unify 4 (strangler): commands migrate in slices; until one
does, the hand-written parser and `TOOLS` entry stay the source of truth, and
`tests/test_command_registry.py` proves that a `Command` declared to match one generates
the same argparse help and the same `tools/list` schema.

Standard library only. Nothing here imports a surface, so `mcp.py` and the parsers can
both depend on it.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

#: The per-CALL identity override every tool but `ddflow_identify` accepts (see mcp.py).
AS_AGENT = "as_agent"
AS_AGENT_SPEC: tuple[str, str, bool] = ("string", "A subagent's own name, this call only.", False)

#: JSON types a parameter may declare.
TYPES = ("string", "boolean", "integer", "number", "array")

_UNSET: Any = object()

#: The longest CLI path: `group command`.
GROUP_DEPTH = 2
#: `via` is a tool, optionally with the selector value.
MAX_VIA = 2


@dataclass(frozen=True)
class Param:
    """One parameter of a command: a CLI argument and an MCP property in one declaration.

    ``help`` is the MCP property description; ``cli_help`` is the argparse help when it
    reads differently (they do today, and a migration may not change either). ``flag`` is
    the option string; left unset it is ``--name`` with underscores as dashes. A
    ``positional`` parameter has no flag. ``repeat`` makes it a list: ``action="append"``
    on the CLI, ``{"type": "array", "items": {"type": "string"}}`` in the schema.
    """

    name: str
    type: str = "string"
    help: str = ""
    required: bool = False
    default: Any = _UNSET
    flag: str | None = None
    positional: bool = False
    choices: tuple[str, ...] | None = None
    repeat: bool = False
    cli_help: str | None = None
    #: Whether ``choices`` also appear as ``enum`` in the schema. Today's schemas do not
    #: carry them, and a migration may not change the bytes `tools/list` serves.
    schema_enum: bool = False
    #: A custom argparse action (e.g. the repeatable ``--globs``); wins over ``repeat``.
    action: Any = None
    #: Not offered on the MCP surface / on the CLI.
    cli_only: bool = False
    mcp_only: bool = False

    def __post_init__(self) -> None:
        if self.type not in TYPES:
            raise ValueError(f"param {self.name!r}: type {self.type!r} not in {TYPES}")
        if self.repeat and self.type != "array":
            object.__setattr__(self, "type", "array")
        if self.positional and self.default is not _UNSET:
            raise ValueError(f"param {self.name!r}: a positional has no default (no nargs)")
        if self.positional and not self.required:
            # argparse refuses a missing positional, so the schema must say it is required.
            object.__setattr__(self, "required", True)
        if self.cli_only and self.mcp_only:
            raise ValueError(f"param {self.name!r} is neither on the CLI nor on MCP")

    @property
    def option(self) -> str:
        return self.flag or "--" + self.name.replace("_", "-")

    def schema(self) -> dict[str, Any]:
        """The JSON Schema (2020-12) property for this parameter. Key order is the order
        `tools/list` has always served: type, description, then items / enum."""
        out: dict[str, Any] = {"type": self.type, "description": self.help}
        if self.type == "array":
            # A schema without `items` is refused by some clients' validators.
            out["items"] = {"type": "string"}
        if self.choices and self.schema_enum:
            out["enum"] = list(self.choices)
        return out

    def add_to(self, parser: argparse.ArgumentParser) -> None:
        """Add this parameter to ``parser``."""
        kwargs: dict[str, Any] = {}
        help_ = self.help if self.cli_help is None else self.cli_help
        if help_:
            kwargs["help"] = help_
        if self.positional:
            args: tuple[str, ...] = (self.name,)
        else:
            args = (self.option,)
            if self.required:
                kwargs["required"] = True
            if self.flag is not None:
                kwargs["dest"] = self.name  # a custom flag must still land on the param's name
        if self.action is not None:
            kwargs["action"] = self.action
        elif self.type == "boolean":
            kwargs["action"] = "store_true"
        elif self.repeat:
            kwargs["action"] = "append"
        if self.type == "integer":
            kwargs["type"] = int
        elif self.type == "number":
            kwargs["type"] = float
        if self.choices is not None:
            kwargs["choices"] = list(self.choices)
        if self.default is not _UNSET:
            kwargs["default"] = self.default
        parser.add_argument(*args, **kwargs)

    def spec(self) -> tuple[str, str, bool]:
        """The ``(json_type, description, required)`` tuple the MCP engine's tool table holds."""
        return (self.type, self.help, self.required)


def params_schema(
    params: Mapping[str, Param] | tuple[Param, ...], *, deprecated: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """The tool ``inputSchema`` for these parameters, each rendered by `Param.schema`.

    A deprecated argument is still ACCEPTED (D-compat) but is not advertised.
    """
    old = deprecated or {}
    items = params.values() if isinstance(params, Mapping) else params
    shown = [p for p in items if p.name not in old]
    required = [p.name for p in shown if p.required]
    return {
        "type": "object",
        "properties": {p.name: p.schema() for p in shown},
        **({"required": required} if required else {}),
        "additionalProperties": False,
    }


def properties_schema(
    props: Mapping[str, tuple[str, str, bool]], *, deprecated: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """`params_schema` for the engine's ``{name: (type, description, required)}`` table, so
    the table-driven tools and `Command.input_schema` share one rendering."""
    return params_schema(
        tuple(Param(n, type=t, help=d, required=req) for n, (t, d, req) in props.items()),
        deprecated=deprecated,
    )


@dataclass(frozen=True)
class Command:
    """One operation, declared once.

    ``path`` is the CLI words (``("gate", "record")``); ``tool`` the MCP tool name (empty:
    not exposed, with ``reason`` saying why -- the parity test's exemption as a field).
    ``call(repo, args, agent)`` returns an `Outcome`; ``payload`` is the field tuple both
    surfaces print; ``prose`` says the MCP body is text, not JSON; ``tier`` is the MCP
    tool tier and ``render`` the optional human renderer. ``tier`` and ``render`` are
    declared here for the slices that generate the tier table (`tools/tiers.py`) and the
    CLI's human output; `tool_entry` does not carry them because the engine reads tiers
    from the tier table, not from the entry.
    """

    path: tuple[str, ...]
    summary: str = ""
    description: str = ""
    params: tuple[Param, ...] = ()
    tool: str = ""
    call: Callable[..., Any] | None = None
    payload: str | tuple[str, ...] = ""
    prose: bool = False
    tier: str = "standard"
    render: Callable[..., Any] | None = None
    handler: Callable[..., Any] | None = None
    kind: str = ""
    identify: bool = False
    deprecated: Mapping[str, Any] = field(default_factory=dict)
    #: Why this command has no MCP tool of its own (the parity exemption), or "".
    reason: str = ""
    #: The tool that serves this command when it has none of its own, and the value of the
    #: argument that selects it: ``("ddflow_list", "task")``; a one-element form names a
    #: tool that covers it whole (``init`` -> ``("ddflow_setup",)``).
    via: tuple[str, ...] = ()
    #: CLI flags this command's tool omits on purpose: ``{"--force": reason}``.
    flag_exempt: Mapping[str, str] = field(default_factory=dict)
    #: Why the body is text and not JSON (required of every ``prose`` tool).
    prose_reason: str = ""

    def __post_init__(self) -> None:
        names = [p.name for p in self.params]
        if len(set(names)) != len(names):
            raise ValueError(f"{'/'.join(self.path)}: duplicate parameter names")
        if not self.path and not self.tool:
            raise ValueError("a command needs a CLI path or an MCP tool name")
        if len(self.via) > MAX_VIA:
            raise ValueError(f"{'/'.join(self.path)}: via is (tool,) or (tool, selector)")

    @property
    def mcp_params(self) -> tuple[Param, ...]:
        return tuple(p for p in self.params if not p.cli_only)

    @property
    def cli_params(self) -> tuple[Param, ...]:
        return tuple(p for p in self.params if not p.mcp_only)

    def properties(self) -> dict[str, tuple[str, str, bool]]:
        """The tool-table ``properties`` (without ``as_agent``, which the engine adds)."""
        return {p.name: p.spec() for p in self.mcp_params}

    def input_schema(self) -> dict[str, Any]:
        """The MCP ``inputSchema``, ``as_agent`` included for every tool but identify."""
        params = self.mcp_params
        if not self.identify:
            t, d, req = AS_AGENT_SPEC
            params = (*params, Param(AS_AGENT, type=t, help=d, required=req))
        return params_schema(params, deprecated=self.deprecated)

    def tool_listing(self) -> dict[str, Any]:
        """One entry of the ``tools/list`` result."""
        return {
            "name": self.tool,
            "description": self.description,
            "inputSchema": self.input_schema(),
        }

    def tool_entry(self) -> dict[str, Any]:
        """The entry the MCP engine's ``TOOLS`` table holds for this command."""
        entry: dict[str, Any] = {
            "description": self.description,
            "properties": self.properties(),
            "api": self.call,
            "payload": self.payload,
        }
        if self.prose:
            entry["text"] = True
        if self.kind:
            entry["kind"] = self.kind
        if self.identify:
            entry["identify"] = True
        if self.deprecated:
            entry["deprecated"] = dict(self.deprecated)
        return entry

    def add_to(self, subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
        """Add this command's subparser to ``subparsers`` (the parent group's)."""
        kwargs: dict[str, Any] = {}
        if self.summary:
            kwargs["help"] = self.summary
        sub = subparsers.add_parser(self.path[-1], **kwargs)
        for p in self.cli_params:
            p.add_to(sub)
        if self.handler is not None:
            sub.set_defaults(fn=self.handler)
        return sub


def add_commands(
    subparsers: argparse._SubParsersAction,
    commands: tuple[Command, ...] | list[Command],
    *,
    groups: Mapping[str, str] | None = None,
) -> None:
    """Register ``commands`` on the root ``subparsers``, in order.

    A command whose path is longer than one word goes under a group parser, created on
    first use with the one-line help ``groups`` gives it. Only paths of one or two words
    exist today.
    """
    made: dict[str, argparse._SubParsersAction] = {}
    for cmd in commands:
        if not cmd.path:
            continue
        if len(cmd.path) == 1:
            cmd.add_to(subparsers)
            continue
        if len(cmd.path) != GROUP_DEPTH:
            raise ValueError(f"{'/'.join(cmd.path)}: only one- and two-word paths are supported")
        group = cmd.path[0]
        if group not in made:
            gp = subparsers.add_parser(group, help=(groups or {}).get(group, ""))
            made[group] = gp.add_subparsers(dest=f"{group}_cmd", required=True)
        cmd.add_to(made[group])


# -- what the declarations say, derived (the parity test reads these) -----------------


def exempt_paths(commands: tuple[Command, ...]) -> dict[tuple[str, ...], str]:
    """CLI paths with no tool of their own, each with its reason. A command routed by a
    selector (``via`` of two) is served, not exempt, even when it also carries a reason."""
    return {c.path: c.reason for c in commands if c.path and c.reason and len(c.via) != MAX_VIA}


def routed_paths(commands: tuple[Command, ...]) -> dict[tuple[str, ...], tuple[str, str]]:
    """CLI paths served by another tool's selector: ``path -> (tool, selector value)``."""
    return {c.path: (c.via[0], c.via[1]) for c in commands if c.path and len(c.via) == MAX_VIA}


def covering_tools(commands: tuple[Command, ...]) -> dict[str, tuple[str, ...]]:
    """One-word commands a differently named tool covers whole: ``init -> (ddflow_setup,)``."""
    return {c.path[0]: c.via for c in commands if len(c.path) == 1 and len(c.via) == 1}


def declared_words(commands: tuple[Command, ...]) -> set[str]:
    """One-word commands exempt from MCP outright (a reason, no tool covering them)."""
    return {c.path[0] for c in commands if len(c.path) == 1 and c.reason and not c.via}


def flag_exemptions(commands: tuple[Command, ...]) -> dict[tuple[str, str], str]:
    """``(tool, flag) -> reason`` for every flag a tool omits on purpose."""
    return {(c.tool, f): r for c in commands for f, r in c.flag_exempt.items()}


def prose_reasons(commands: tuple[Command, ...]) -> dict[str, str]:
    """``tool -> reason`` for every tool whose body is text."""
    return {c.tool: c.prose_reason for c in commands if c.tool and c.prose}

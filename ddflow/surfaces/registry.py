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
    tool tier; ``render`` is the optional human renderer.
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
    #: Why this command is absent from a surface (the parity exemption), or "".
    reason: str = ""

    def __post_init__(self) -> None:
        names = [p.name for p in self.params]
        if len(set(names)) != len(names):
            raise ValueError(f"{'/'.join(self.path)}: duplicate parameter names")
        if not self.path and not self.tool:
            raise ValueError("a command needs a CLI path or an MCP tool name")

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

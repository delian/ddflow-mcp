"""A command parameter, one declaration for the CLI argument and the MCP property.

`Param` names a parameter once; `params_schema` and `properties_schema` render the MCP
``inputSchema`` and the engine's ``{name: (type, description, required)}`` table from the
same declaration. `AS_AGENT`/`AS_AGENT_SPEC` are the per-call identity parameter every
tool but `ddflow_identify` accepts.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ...config_sections._compat import MIN_REMOVED_IN
from ._aliases import Alias, _check_aliases, _hide_from_abbreviation

#: The per-CALL identity override every tool but `ddflow_identify` accepts (see mcp.py).
AS_AGENT = "as_agent"
AS_AGENT_SPEC: tuple[str, str, bool] = ("string", "Subagent name, this call only.", False)

#: JSON types a parameter may declare.
TYPES = ("string", "boolean", "integer", "number", "array")

_UNSET: Any = object()

#: The ``nargs`` of a positional that argparse does not require.
OPTIONAL_NARGS = ("?", "*")


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
    #: argparse's ``nargs`` (``"?"``: optional, so not required on MCP). On a flag, ``"?"``
    #: makes its value optional: ``--apply`` alone is ``const``, ``--apply repairs`` is
    #: ``repairs``, and left off it is ``default`` (``None``).
    nargs: str | None = None
    const: Any = _UNSET
    #: Whether the MCP property is required when that differs from the CLI flag (a prompt's
    #: ``--text`` is read from stdin when absent; the tool has no stdin).
    tool_required: bool | None = None
    #: argparse's ``metavar`` (``--extends ID``), and the name of the mutually exclusive
    #: group (``add_mutually_exclusive_group``) the flag belongs to: every parameter of a
    #: command sharing the name is in one group.
    metavar: str | None = None
    exclusive: str | None = None
    #: One member of an ``exclusive`` group says so: the group is required (exactly one of
    #: its members must be given).
    exclusive_required: bool = False
    #: argparse's ``type`` when the tool's JSON type differs (``--exit-code`` is an int on
    #: the command line and a string in the tool's schema).
    cli_type: Callable[[str], Any] | None = None
    #: Earlier names that still work (D-compat): as a tool argument, and as the CLI flag
    #: ``--name`` spelled the way `option` is (a name starting with ``-`` is a CLI-only flag
    #: spelling taken as written). Hidden from help and the schema; needs ``deprecated_since``.
    aliases: tuple[str, ...] = ()
    deprecated_since: str = ""
    removed_in: str = MIN_REMOVED_IN
    #: What an old name should be replaced by when that is not simply this parameter; shown
    #: verbatim in the notice (spell a flag's as the flag, ``--name``).
    replacement: str = ""
    #: A one-letter CLI spelling of the flag (``-m`` for ``--message``), CLI only.
    short: str = ""
    #: The shell-completion value kind of this argument (`id:<record kind>` for record ids,
    #: ``phase``/``gate``/``agent``/``state``/``kind``, ``path``/``dir``, ``enum`` from
    #: ``choices``, or ``none`` for free text). Unset: inferred from the name, the choices
    #: and the type by `services.completion.kinds.infer` (`ddflow/services/completion/kinds.py`).
    #: Short spellings are normalized (``complete="phase"`` -> ``id:phase``) and attached
    #: onto the argparse action, which the completion engine (`services.completion.values`)
    #: reads when it builds the completers for the argument.
    complete: str = ""

    def __post_init__(self) -> None:
        if self.positional and any(a.startswith("-") for a in self.aliases):
            raise ValueError(f"param {self.name!r}: a positional has no flag to alias")
        _check_aliases(
            f"param {self.name!r}",
            self.aliases,
            self.name,
            self.deprecated_since,
            self.removed_in,
        )
        if self.type not in TYPES:
            raise ValueError(f"param {self.name!r}: type {self.type!r} not in {TYPES}")
        if self.repeat and self.type != "array":
            object.__setattr__(self, "type", "array")
        self._check_positional()
        if self.positional and not self.required and self.nargs is None:
            # argparse refuses a missing positional, so the schema must say it is required.
            object.__setattr__(self, "required", True)
        if self.cli_only and self.mcp_only:
            raise ValueError(f"param {self.name!r} is neither on the CLI nor on MCP")

    def _check_positional(self) -> None:
        """What only a positional, an ``nargs``, a ``const`` or an exclusive group may be."""
        if self.positional and self.default is not _UNSET and self.nargs not in OPTIONAL_NARGS:
            raise ValueError(f"param {self.name!r}: a positional has no default (no nargs)")
        if self.nargs is not None and not self.positional and self.nargs != "?":
            raise ValueError(f"param {self.name!r}: a flag takes nargs='?' (an optional value)")
        if self.nargs == "?" and not self.positional and self.const is _UNSET:
            raise ValueError(f"param {self.name!r}: a flag with nargs='?' needs a const")
        if self.const is not _UNSET and (self.positional or self.nargs != "?"):
            raise ValueError(f"param {self.name!r}: const goes with a flag's nargs='?'")
        if self.exclusive_required and self.exclusive is None:
            raise ValueError(f"param {self.name!r}: exclusive_required needs an exclusive group")
        if self.nargs not in (None, *OPTIONAL_NARGS):
            raise ValueError(f"param {self.name!r}: only nargs='?' or '*' (optional) is supported")
        if self.exclusive is not None and (self.required or self.positional):
            raise ValueError(f"param {self.name!r}: a member of an exclusive group is optional")

    @property
    def mcp_required(self) -> bool:
        return self.required if self.tool_required is None else self.tool_required

    @property
    def option(self) -> str:
        return self.flag or "--" + self.name.replace("_", "-")

    def arg_aliases(self) -> dict[str, Alias]:
        """Old tool-argument names -> the `Alias` that says so (those a flag spelling is not)."""
        return {
            a: Alias(
                "argument", a, self.replacement or self.name, self.deprecated_since, self.removed_in
            )
            for a in self.aliases
            if not a.startswith("-")
        }

    def flag_aliases(self) -> dict[str, Alias]:
        """Old CLI option strings -> their `Alias`, each spelled as argparse is given it."""
        if self.positional:
            return {}
        return {
            (a if a.startswith("-") else "--" + a.replace("_", "-")): Alias(
                "flag",
                a if a.startswith("-") else "--" + a.replace("_", "-"),
                self.replacement or self.option,
                self.deprecated_since,
                self.removed_in,
            )
            for a in self.aliases
        }

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

    def _value_kwargs(self) -> dict[str, Any]:
        """What argparse is told of the VALUE: its action, type, choices and default."""
        kwargs: dict[str, Any] = {}
        if self.action is not None:
            kwargs["action"] = self.action
        elif self.type == "boolean":
            kwargs["action"] = "store_true"
        elif self.repeat:
            kwargs["action"] = "append"
        if self.cli_type is not None:
            kwargs["type"] = self.cli_type
        elif self.type == "integer":
            kwargs["type"] = int
        elif self.type == "number":
            kwargs["type"] = float
        if self.choices is not None:
            kwargs["choices"] = list(self.choices)
        if self.default is not _UNSET:
            kwargs["default"] = self.default
        if self.const is not _UNSET:
            kwargs["const"] = self.const
        if self.metavar is not None:
            kwargs["metavar"] = self.metavar
        return kwargs

    def add_to(self, parser: argparse.ArgumentParser, groups: dict[str, Any] | None = None) -> None:
        """Add this parameter to ``parser``. ``groups`` holds the mutually exclusive groups
        made so far for this parser (a parameter naming one joins it, a new name makes it)."""
        kwargs: dict[str, Any] = self._value_kwargs()
        help_ = self.help if self.cli_help is None else self.cli_help
        if help_:
            kwargs["help"] = help_
        if self.positional:
            args: tuple[str, ...] = (self.name,)
            if self.nargs is not None:
                kwargs["nargs"] = self.nargs
        else:
            args = (self.short, self.option) if self.short else (self.option,)
            if self.nargs is not None:
                kwargs["nargs"] = self.nargs
            if self.required:
                kwargs["required"] = True
            if self.flag is not None or self.short:
                kwargs["dest"] = self.name  # a custom flag must still land on the param's name
        target: Any = parser
        if self.exclusive is not None:
            groups = {} if groups is None else groups
            if self.exclusive not in groups:
                groups[self.exclusive] = parser.add_mutually_exclusive_group()
            target = groups[self.exclusive]
        action = target.add_argument(*args, **kwargs)
        # The declared completion value kind, normalized to its canonical spelling
        # (`complete="phase"` -> `id:phase`), attached onto the action for the completion
        # engine to read. Empty: inferred from the name/choices/type at walk time, and never
        # attached, so a hand-written `add_argument` cannot collide with it.
        if self.complete:
            from ...services.completion import kinds

            action.complete = kinds.normalize(self.complete)  # type: ignore[attr-defined]
        # An old flag is the SAME action under another option string: parsing finds it,
        # `--help` (which prints the action's own strings) does not.
        for option, alias in self.flag_aliases().items():
            if option in parser._option_string_actions:
                raise ValueError(f"alias {option} of {self.name!r} is already an option")
            parser._option_string_actions[option] = action
            if not hasattr(parser, "_compat_flags"):
                parser._compat_flags = {}  # type: ignore[attr-defined]
                _hide_from_abbreviation(parser)
            parser._compat_flags[option] = alias  # type: ignore[attr-defined]

    def spec(self) -> tuple[str, str, bool]:
        """The ``(json_type, description, required)`` tuple the MCP engine's tool table holds."""
        return (self.type, self.help, self.mcp_required)


def params_schema(
    params: Mapping[str, Param] | tuple[Param, ...], *, deprecated: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """The tool ``inputSchema`` for these parameters, each rendered by `Param.schema`.

    A deprecated argument is still ACCEPTED (D-compat) but is not advertised.
    """
    old = deprecated or {}
    items = params.values() if isinstance(params, Mapping) else params
    shown = [p for p in items if p.name not in old]
    required = [p.name for p in shown if p.mcp_required]
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

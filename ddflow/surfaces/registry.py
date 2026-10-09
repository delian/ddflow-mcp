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
import dataclasses
import difflib
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ..config_sections._compat import MIN_REMOVED_IN, check_rename

#: The per-CALL identity override every tool but `ddflow_identify` accepts (see mcp.py).
AS_AGENT = "as_agent"
AS_AGENT_SPEC: tuple[str, str, bool] = ("string", "A subagent's own name, this call only.", False)

#: JSON types a parameter may declare.
TYPES = ("string", "boolean", "integer", "number", "array")

_UNSET: Any = object()

#: The ``nargs`` of a positional that argparse does not require.
OPTIONAL_NARGS = ("?", "*")

#: The longest CLI path: `group command`.
GROUP_DEPTH = 2
#: `via` is a tool, optionally with the selector value.
MAX_VIA = 2


# -- aliases and deprecations (D-compat) ---------------------------------------------

#: What an old name is, in a notice: a CLI command (group), a CLI flag, an MCP tool, a tool argument.
KINDS = ("command", "flag", "tool", "argument")

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")


@dataclass(frozen=True)
class Alias:
    """An old name that still works: what it was, what replaced it, and since when.

    Calling by it works; the first use in a session says so in one line (`Notices`); it is
    hidden from ``--help`` and ``tools/list`` and is never removed before 1.0 (`removed_in`
    is checked wherever one is declared).
    """

    kind: str
    old: str
    new: str
    since: str
    removed_in: str = MIN_REMOVED_IN

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"alias {self.old!r}: kind {self.kind!r} not in {KINDS}")
        if not self.old or self.old == self.new:
            raise ValueError(f"alias {self.old!r} must differ from {self.new!r}")
        check_rename(f"{self.kind} {self.old!r}", self.since, self.removed_in)

    def notice(self) -> str:
        """The one line a caller reads. No prefix: the CLI adds ``ddflow: ``, MCP ``note: ``."""
        return (
            f"{self.kind} '{self.old}' is deprecated since {self.since}; use '{self.new}' "
            f"(the old name keeps working until {self.removed_in})."
        )


class Notices:
    """Which aliases a session has already been told about: once each (D-compat)."""

    def __init__(self) -> None:
        self._seen: set[Alias] = set()

    def fresh(self, aliases: Iterable[Alias]) -> list[Alias]:
        """The aliases among ``aliases`` not reported before, now marked reported."""
        out: list[Alias] = []
        for a in aliases:
            if a not in self._seen:
                self._seen.add(a)
                out.append(a)
        return out


def closest(name: str, candidates: Iterable[str]) -> str:
    """The candidate nearest to ``name`` for a 'did you mean', or "" when none is near."""
    near = difflib.get_close_matches(name, sorted(set(candidates)), n=1, cutoff=0.6)
    return near[0] if near else ""


def _did_you_mean(name: str, candidates: Iterable[str]) -> str:
    near = closest(name, candidates)
    return f" Did you mean '{near}'?" if near else ""


def _check_aliases(what: str, aliases: tuple[str, ...], own: str, since: str, removed: str) -> None:
    if not aliases:
        return
    check_rename(what, since, removed)
    if len(set(aliases)) != len(aliases) or own in aliases:
        raise ValueError(f"{what}: aliases must be distinct and differ from {own!r}")
    if bad := [a for a in aliases if not _WORD.fullmatch(a.lstrip("-"))]:
        raise ValueError(f"{what}: {bad[0]!r} is not a usable alias")


class AliasChoices(dict):
    """A subparsers' ``choices`` that can also answer to hidden alias names.

    Lookup (``in``, ``[]``, ``get``) finds an alias; iteration, ``keys``, ``items`` and
    ``len`` do not show it, so ``--help``, usage lines, error messages and every walk over a
    parser list the canonical commands only. The alias maps to the SAME parser.
    """

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.hidden: dict[str, argparse.ArgumentParser] = {}

    def __missing__(self, key: str) -> argparse.ArgumentParser:
        return self.hidden[key]

    def __contains__(self, key: object) -> bool:
        return super().__contains__(key) or key in self.hidden

    def get(self, key: str, default: Any = None) -> Any:
        return self[key] if key in self else default


def _alias_table(subparsers: argparse._SubParsersAction) -> AliasChoices:
    """``subparsers.choices`` as an `AliasChoices`, replacing the plain dict on first use."""
    if not isinstance(subparsers.choices, AliasChoices):
        both = AliasChoices(subparsers._name_parser_map)
        subparsers._name_parser_map = both
        subparsers.choices = both
    return subparsers.choices  # type: ignore[return-value]


def add_command_alias(
    subparsers: argparse._SubParsersAction,
    parser: argparse.ArgumentParser,
    word: str,
    alias: Alias,
) -> None:
    """Make the one word ``word`` reach ``parser`` under ``subparsers``, hidden; ``alias``
    is what a caller who typed it is told."""
    table = _alias_table(subparsers)
    if word in table:
        raise ValueError(f"alias {word!r} is already a command or alias here")
    table.hidden[word] = parser
    if not hasattr(subparsers, "_compat"):
        subparsers._compat = {}  # type: ignore[attr-defined]
    subparsers._compat[word] = alias  # type: ignore[attr-defined]


def _hide_from_abbreviation(parser: argparse.ArgumentParser) -> None:
    """Keep ``parser``'s old flags out of argparse's prefix matching: `--do` matched both
    `--docs` and its old name `--doc` (the same action) and was refused as ambiguous. The old
    flag still matches EXACTLY; only an abbreviation of it is not offered."""
    matches = parser._get_option_tuples

    def visible(option_string: str) -> list[Any]:
        found = matches(option_string)
        if option_string[:2] != option_string[:1] * 2:
            return found  # `-dv`: a short option with its value attached is not an abbreviation
        old = parser._compat_flags  # type: ignore[attr-defined]
        return [m for m in found if m[1] not in old]

    parser._get_option_tuples = visible  # type: ignore[method-assign]


class SuggestingParser(argparse.ArgumentParser):
    """The root parser: an unknown command gets a 'did you mean'. Its subparsers inherit it,
    and every level records the command word taken (`_WordsRecorder`)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.register("action", "parsers", _WordsRecorder)

    def error(self, message: str) -> Any:
        for a in self._actions:
            # only the command word: another option's bad value is not a command name
            if isinstance(a, argparse._SubParsersAction) and (
                m := re.match(rf"argument {re.escape(a.dest)}: invalid choice: '([^']*)'", message)
            ):
                message += _did_you_mean(m.group(1), list(a.choices))
        super().error(message)


def used_aliases(
    parser: argparse.ArgumentParser, args: argparse.Namespace, argv: Iterable[str] = ()
) -> list[Alias]:
    """The aliases a parsed command line went through: the command words typed (from the
    namespace the subparsers filled) and the flags typed (from ``argv``, up to ``--``)."""
    out: list[Alias] = []
    node = parser
    flags: dict[str, Alias] = dict(getattr(node, "_compat_flags", {}))
    while True:
        sub = next((a for a in node._actions if isinstance(a, argparse._SubParsersAction)), None)
        typed = getattr(args, sub.dest, None) if sub is not None else None
        if sub is None or typed is None or typed not in sub.choices:
            break
        if hit := getattr(sub, "_compat", {}).get(typed):
            out.append(hit)
        node = sub.choices[typed]
        flags.update(getattr(node, "_compat_flags", {}))
    for token in argv:
        if token == "--":
            break
        if (hit := flags.get(token.split("=", 1)[0])) and hit not in out:
            out.append(hit)
    return out


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
            args = (self.option,)
            if self.nargs is not None:
                kwargs["nargs"] = self.nargs
            if self.required:
                kwargs["required"] = True
            if self.flag is not None:
                kwargs["dest"] = self.name  # a custom flag must still land on the param's name
        target: Any = parser
        if self.exclusive is not None:
            groups = {} if groups is None else groups
            if self.exclusive not in groups:
                groups[self.exclusive] = parser.add_mutually_exclusive_group()
            target = groups[self.exclusive]
        action = target.add_argument(*args, **kwargs)
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
    #: True, or a predicate over the call's arguments (``render``: text only with ``show``).
    prose: bool | Callable[..., bool] = False
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
    #: Earlier names that still work (D-compat): ``aliases`` are old spellings of the LAST
    #: word of ``path`` (a renamed group is `add_commands`' ``group_aliases``), ``tool_aliases``
    #: old MCP tool names. Both are hidden from ``--help`` and ``tools/list``, always
    #: callable, and need ``deprecated_since``.
    aliases: tuple[str, ...] = ()
    tool_aliases: tuple[str, ...] = ()
    deprecated_since: str = ""
    removed_in: str = MIN_REMOVED_IN
    #: What an old name should be replaced by when that is not simply this command.
    replacement: str = ""
    #: The order ``tools/list`` has always served the properties in, when it is not the order
    #: of ``params`` (which is the CLI's, and so the order of ``--help``). Empty: the same.
    tool_order: tuple[str, ...] = ()
    #: Attributes the CLI parser sets on every parse of this command, beside the handler.
    defaults: Mapping[str, Any] = field(default_factory=dict)
    #: The tool is told where the caller stands (``called_from``), for a tool that creates
    #: or lands a worktree (the engine reads ``wants_called_from`` from the table entry).
    wants_called_from: bool = False
    #: The tool reports progress while it runs (``on_progress``), for a call that takes minutes.
    wants_progress: bool = False
    #: argparse's ``formatter_class`` and ``epilog`` for this command's help.
    formatter_class: Any = None
    epilog: str = ""

    def __post_init__(self) -> None:
        self._check_names()
        self._check_params()
        if not self.path and not self.tool:
            raise ValueError("a command needs a CLI path or an MCP tool name")
        if len(self.via) > MAX_VIA:
            raise ValueError(f"{'/'.join(self.path)}: via is (tool,) or (tool, selector)")

    def _check_names(self) -> None:
        """The old names this command answers to are well formed."""
        _check_aliases(
            f"{'/'.join(self.path) or self.tool}",
            self.aliases,
            self.path[-1] if self.path else "",
            self.deprecated_since,
            self.removed_in,
        )
        _check_aliases(
            f"tool {self.tool}",
            self.tool_aliases,
            self.tool,
            self.deprecated_since,
            self.removed_in,
        )
        if self.aliases and not self.path:
            raise ValueError(f"{self.tool}: CLI aliases need a CLI path")
        if self.tool_aliases and not self.tool:
            raise ValueError(f"{'/'.join(self.path)}: tool aliases need a tool")

    def _check_params(self) -> None:
        """The parameters are distinct, and ``tool_order`` names each tool one once."""
        names = [p.name for p in self.params]
        if len(set(names)) != len(names):
            raise ValueError(f"{'/'.join(self.path)}: duplicate parameter names")
        spelled = names + [a for p in self.params for a in p.aliases]
        if len(set(spelled)) != len(spelled):
            raise ValueError(
                f"{'/'.join(self.path) or self.tool}: a parameter alias is also another "
                "parameter's name or alias"
            )
        tool_names = sorted(p.name for p in self.params if not p.cli_only)
        if self.tool_order and sorted(self.tool_order) != tool_names:
            raise ValueError(
                f"{self.tool or '/'.join(self.path)}: tool_order must name each tool parameter once"
            )

    @property
    def mcp_params(self) -> tuple[Param, ...]:
        own = tuple(p for p in self.params if not p.cli_only)
        if not self.tool_order:
            return own
        by_name = {p.name: p for p in own}
        return tuple(by_name[n] for n in self.tool_order if n in by_name)

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
            entry["text"] = self.prose if callable(self.prose) else True
        if self.kind:
            entry["kind"] = self.kind
        if self.identify:
            entry["identify"] = True
        if self.wants_called_from:
            entry["wants_called_from"] = True
        if self.wants_progress:
            entry["wants_progress"] = True
        if self.deprecated:
            entry["deprecated"] = dict(self.deprecated)
        if aliases := self.tool_alias_list():
            entry["aliases"] = aliases
        if arg_aliases := {a: al for p in self.mcp_params for a, al in p.arg_aliases().items()}:
            entry["arg_aliases"] = arg_aliases
        return entry

    def tool_alias_list(self) -> tuple[Alias, ...]:
        """The old names of this command's MCP tool, as `Alias`es."""
        return tuple(
            Alias("tool", a, self.replacement or self.tool, self.deprecated_since, self.removed_in)
            for a in self.tool_aliases
        )

    def add_to(self, subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
        """Add this command's subparser to ``subparsers`` (the parent group's)."""
        kwargs: dict[str, Any] = {}
        if self.summary:
            kwargs["help"] = self.summary
        if self.formatter_class is not None:
            kwargs["formatter_class"] = self.formatter_class
        if self.epilog:
            kwargs["epilog"] = self.epilog
        sub = subparsers.add_parser(self.path[-1], **kwargs)
        groups: dict[str, Any] = {
            p.exclusive: sub.add_mutually_exclusive_group(required=True)
            for p in self.cli_params
            if p.exclusive and p.exclusive_required
        }
        for p in self.cli_params:
            p.add_to(sub, groups)
        if self.handler is not None:
            sub.set_defaults(fn=self.handler)
        if self.defaults:
            sub.set_defaults(**self.defaults)
        for word in self.aliases:
            old = " ".join((*self.path[:-1], word))
            add_command_alias(
                subparsers,
                sub,
                word,
                Alias(
                    "command",
                    old,
                    self.replacement or " ".join(self.path),
                    self.deprecated_since,
                    self.removed_in,
                ),
            )
        return sub


def _group_subparsers(
    subparsers: argparse._SubParsersAction, group: str
) -> argparse._SubParsersAction | None:
    """The subparsers action of a group parser already on ``subparsers``, or None."""
    parser = subparsers.choices.get(group)
    if parser is None:
        return None
    return next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)


def add_commands(
    subparsers: argparse._SubParsersAction,
    commands: tuple[Command, ...] | list[Command],
    *,
    groups: Mapping[str, str] | None = None,
    group_aliases: Mapping[str, tuple[Alias, ...]] | None = None,
    handlers: Mapping[tuple[str, ...], Callable[..., Any]] | None = None,
    executor: Callable[[Command], Callable[..., Any]] | None = None,
) -> None:
    """Register ``commands`` on the root ``subparsers``, in order.

    A command whose path is longer than one word goes under a group parser, created on
    first use with the one-line help ``groups`` gives it (a group not named there is not
    listed in the parent's help) or, when ``subparsers`` already holds that group, added to
    it. Only paths of one or two words exist today. ``group_aliases`` maps a group to the
    old names of the whole group (``{"docs": (Alias("command", "doc", "docs", "0.1.17"),)}``):
    hidden, always callable. ``handlers`` supplies the CLI function of a command that
    declares none (its path is the key), so a declaration can live where the parser's
    imports are not wanted. ``executor`` makes the CLI function of a command that has none
    and declares ``render`` (`cliexec.handler`): such a command needs no ``cmd_*`` function.
    """
    made: dict[str, argparse._SubParsersAction] = {}
    parsers: dict[str, argparse.ArgumentParser] = {}
    for declared in commands:
        if not declared.path:
            continue
        cmd = declared
        if handlers and cmd.handler is None and cmd.path in handlers:
            cmd = dataclasses.replace(cmd, handler=handlers[cmd.path])
        if executor and cmd.handler is None and cmd.render is not None:
            cmd = dataclasses.replace(cmd, handler=executor(cmd))
        if len(cmd.path) == 1:
            cmd.add_to(subparsers)
            continue
        if len(cmd.path) != GROUP_DEPTH:
            raise ValueError(f"{'/'.join(cmd.path)}: only one- and two-word paths are supported")
        group = cmd.path[0]
        if group not in made:
            existing = _group_subparsers(subparsers, group)
            if existing is not None:
                if groups and group in groups:
                    raise ValueError(
                        f"group {group!r} was made before; its help is given where it is made"
                    )
                made[group] = existing
                parsers[group] = subparsers.choices[group]
            else:
                kw = {"help": groups[group]} if groups and group in groups else {}
                gp = subparsers.add_parser(group, **kw)
                parsers[group] = gp
                made[group] = gp.add_subparsers(dest=f"{group}_cmd", required=True)
        cmd.add_to(made[group])
    for group, aliases in (group_aliases or {}).items():
        if group not in made:
            raise ValueError(f"group alias for {group!r}, which no command declares")
        for alias in aliases:
            add_command_alias(subparsers, parsers[group], alias.old, alias)


def by_tool(commands: Iterable[Command]) -> dict[str, Command]:
    """The commands that have an MCP tool, by tool name (a tool module takes its entries
    from here: ``by_tool(COMMANDS)["ddflow_x"].tool_entry()``)."""
    return {c.tool: c for c in commands if c.tool}


# -- the MCP side of aliases ----------------------------------------------------------


def resolve_tool(tools: Mapping[str, Mapping[str, Any]], name: str) -> tuple[str, Alias | None]:
    """``(tool name, the alias used)``: ``name`` itself when it is a tool, the tool an alias
    names otherwise, and ``("", None)`` for a name that is neither."""
    if name in tools:
        return name, None
    for tool, spec in tools.items():
        for alias in spec.get("aliases", ()):
            if alias.old == name:
                return tool, alias
    return "", None


def unknown_tool_hint(tools: Mapping[str, Any], name: str) -> str:
    """`` Did you mean 'ddflow_x'?`` for a tool call naming nothing, or ""."""
    return _did_you_mean(name, tools)


def rename_args(
    spec: Mapping[str, Any], args: Mapping[str, Any]
) -> tuple[dict[str, Any], list[Alias], str]:
    """``(args under their current names, the aliases used, an error or "")``.

    An argument spelled with an old name is moved to the current one; giving both is an
    error (which one is meant?).
    """
    out = dict(args)
    used: list[Alias] = []
    for old, alias in (spec.get("arg_aliases") or {}).items():
        if old not in out:
            continue
        if alias.new in out:
            return out, used, f"both '{alias.new}' and its deprecated name '{old}' were given"
        out[alias.new] = out.pop(old)
        used.append(alias)
    return out, used, ""


def unknown_arg_hint(known: Iterable[str], unknown: Iterable[str]) -> str:
    """`` Did you mean 'a' (for 'b')?`` for the unknown arguments that have a near name."""
    known = list(known)  # read once per unknown name
    pairs = [(u, closest(u, known)) for u in unknown]
    said = [f"'{near}' (for '{u}')" for u, near in pairs if near]
    return f" Did you mean {', '.join(said)}?" if said else ""


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


# -- result schemas (D-compat, D-compat-json-views) -----------------------------------

#: The top-level key an object result carries to name its schema: ``"claim@1"``. No payload
#: field is called this (`tests/test_compat_json.py` checks every tool's).
SCHEMA_KEY = "schema"
#: What a tool name loses to become the command name a schema is filed under.
TOOL_PREFIX = "ddflow_"
#: ``command -> n`` for a command whose result shape changed incompatibly (a field removed
#: or retyped) since schema 1. Adding a field never bumps it; the old shape stays available
#: for one minor release. Absent means 1.
SCHEMA_VERSIONS: dict[str, int] = {}
#: The key a non-zero exit's body leads with (`mcp._refusal_body`): any schema may carry it.
REFUSAL_KEY = "refusal"
#: Payload keys whose value is a bare array. These bodies keep their exact shape (no tag);
#: their schema is declared only for the MCP surface. `tests/test_compat_json.py` runs the
#: tools it can and compares.
ARRAY_PAYLOADS = frozenset(
    {
        "hits",
        "found",
        "rows",
        "findings",
        "candidates",
        "due",
        "jobs",
        "observed",
    }
)

#: What a tool's body is, as `result_shape` says.
SHAPES = ("object", "nested", "array", "text", "dynamic")


def command_name(tool: str) -> str:
    """The name a tool's result schema is filed under: ``ddflow_gate_record`` -> ``gate_record``."""
    return tool[len(TOOL_PREFIX) :] if tool.startswith(TOOL_PREFIX) else tool


def schema_version(command: str) -> int:
    """The current version of ``command``'s result schema."""
    return SCHEMA_VERSIONS.get(command, 1)


def schema_tag(command: str) -> str:
    """The value of an object result's ``schema`` key: ``"<command>@<n>"``."""
    return f"{command}@{schema_version(command)}"


def result_shape(payload: Any, *, text: Any = False) -> str:
    """What a tool's body is, from its table entry (`payload` and `text`):

    ``text`` a document, ``dynamic`` decided by the call's arguments (a callable), ``array``
    a bare array (kept exactly as it is), ``nested`` the value of one data field (an object
    that is a record, or null: ``show``, ``decision_show``, ``recall``), ``object`` a projection
    of fields or the whole data dict.
    """
    if text:
        return "dynamic" if callable(text) else "text"
    if callable(payload):
        return "dynamic"
    if isinstance(payload, str) and payload:
        return "array" if payload in ARRAY_PAYLOADS else "nested"
    return "object"


def payload_fields(payload: Any) -> tuple[str, ...]:
    """The field names a projection declares; none for the whole-data and nested forms,
    whose fields the operation decides."""
    return tuple(payload) if isinstance(payload, tuple) else ()


def output_schema(
    command: str, payload: Any, *, text: Any = False, extra: Iterable[str] = ()
) -> dict[str, Any] | None:
    """The JSON Schema (2020-12) of an object result, or None for any other shape.

    Field TYPES are not declared (``{}``: any); the payload field lists name the fields and
    no more, until a type layer exists. Nothing is ``required`` and extra fields are allowed:
    a refusal leads with `refusal`, and a field added within a version must not invalidate
    a result. ``extra`` are the fields a call adds when it has them.
    """
    if result_shape(payload, text=text) != "object":
        return None
    props: dict[str, Any] = {SCHEMA_KEY: {"const": schema_tag(command)}}
    for name in (*payload_fields(payload), *extra, REFUSAL_KEY):
        props.setdefault(name, {})
    return {"type": "object", "properties": props, "additionalProperties": True}


def array_schema(command: str, payload: Any) -> dict[str, Any] | None:
    """The schema of a bare-array result as a document of its own, or None for any other
    shape. A result array has no place to carry a tag, so this is where it is named."""
    if result_shape(payload) != "array":
        return None
    return {
        "title": schema_tag(command),
        "type": "array",
        "items": {},
    }


def result_schemas(
    tools: Mapping[str, Mapping[str, Any]], optional: Iterable[str] = ()
) -> dict[str, dict[str, Any]]:
    """Every tool's declared result: command, tag, shape, payload and generated schemas.

    ``tools`` is the engine's tool table; ``optional`` the fields a projected body adds when
    the call produced them (`mcp._OPTIONAL_KEYS`). The golden file pinning the contract is
    this table (`tests/test_compat_json.py`; ``python -m ddflow.surfaces.tool_table
    --schemas`` prints it).
    """
    table: dict[str, dict[str, Any]] = {}
    optional = tuple(optional)  # read once per tool below
    for tool, spec in tools.items():
        payload, text = spec.get("payload", ""), spec.get("text", False)
        command = command_name(tool)
        row: dict[str, Any] = {
            "command": command,
            "schema": schema_tag(command),
            "shape": result_shape(payload, text=text),
        }
        if isinstance(payload, str) and payload:
            row["payload"] = payload
        elif isinstance(payload, tuple):
            row["payload"] = list(payload)
        extra = optional if isinstance(payload, tuple) else ()
        row["output_schema"] = output_schema(command, payload, text=text, extra=extra)
        row["array_schema"] = array_schema(command, payload)
        table[tool] = row
    return table


def tag_body(body: Any, command: str) -> Any:
    """``body`` with its ``schema`` key placed first when it is an object; any other body
    (an array, text, a scalar, null) comes back unchanged. A refusal body keeps `refusal`
    as its first key (`mcp._refusal_body`: the block a machine reads first, fixed in
    c5c7d9f) and the tag follows it. A body already carrying the key keeps its own value:
    the tag never overwrites a field."""
    if not isinstance(body, dict) or not command or SCHEMA_KEY in body:
        return body
    tag = {SCHEMA_KEY: schema_tag(command)}
    if next(iter(body), None) == REFUSAL_KEY:
        rest = {k: v for k, v in body.items() if k != REFUSAL_KEY}
        return {REFUSAL_KEY: body[REFUSAL_KEY], **tag, **rest}
    return {**tag, **body}


class _WordsRecorder(argparse._SubParsersAction):
    """A subparsers action that also records which command word was taken, on the namespace
    under `WORDS`: ``("gate", "record")``. Read from argparse's own decision, not rebuilt from
    the words typed (an option's value can spell a command, ``--`` makes the rest positional,
    and an option can share a subparser's ``dest``: ``bisect --cmd`` overwrote ``cmd``)."""

    def __call__(self, parser, namespace, values, option_string=None):  # type: ignore[no-untyped-def]
        word = values[0] if isinstance(values, (list, tuple)) else values
        super().__call__(parser, namespace, values, option_string)
        target = self._name_parser_map.get(word)
        # the sub-namespace was copied over ours by now and carries the deeper words
        canonical = next((n for n, p in self.choices.items() if p is target), word)
        setattr(namespace, WORDS, (canonical, *getattr(namespace, WORDS, ())))


#: The namespace attribute `_WordsRecorder` fills.
WORDS = "_command_words"


def parsed_path(args: argparse.Namespace) -> tuple[str, ...]:
    """The command words a parsed command line went through, aliases resolved to the
    command they name: ``("gate", "record")``; empty when no subcommand was taken."""
    return tuple(getattr(args, WORDS, ()))


#: CLI commands whose words are not the name of the tool that serves them and which no
#: `via` declares: ``ddflow research`` (no verb) files what ``ddflow_research_add`` files.
CLI_COMMAND_NAMES: dict[tuple[str, ...], str] = {
    ("research",): "research_add",
    ("companions",): "companions",
    ("companions", "list"): "companions",
    ("hooks",): "hooks",
    ("hooks", "status"): "hooks",
    ("hooks", "install"): "hooks",
    ("hooks", "uninstall"): "hooks",
    ("prompts",): "prompts",
    ("prompts", "list"): "prompts",
    ("prompts", "get"): "prompts",
    ("prompts", "show"): "prompts",
}
#: A flag that makes a command another tool's: ``(path, parsed attribute) -> name``. Read from
#: what argparse parsed (an abbreviation, ``--verif``, sets the same attribute), not from the
#: words typed.
CLI_FLAG_COMMANDS: dict[tuple[tuple[str, ...], str], str] = {
    (("companions",), "verify"): "companions_verify",
    (("companions", "list"), "verify"): "companions_verify",
    (("import",), "verify"): "import_verify",
    (("doctor",), "upgrade"): "upgrade",
}


def command_for_path(
    path: tuple[str, ...],
    routed: Mapping[tuple[str, ...], tuple[str, str]],
    covering: Mapping[str, tuple[str, ...]],
    args: argparse.Namespace | None = None,
) -> str:
    """The schema name of a CLI command: the name of the MCP tool that serves it, so the two
    surfaces tag one result alike. ``routed`` maps a path a selector serves to its tool
    (``task list`` -> ``ddflow_list``), ``covering`` a one-word command a differently named
    tool covers (``init`` -> ``ddflow_setup``), ``args`` the parsed line for a flag that picks the
    tool; anything else is its words joined by ``_``."""
    for (where, dest), name in CLI_FLAG_COMMANDS.items():
        if where == path and args is not None and getattr(args, dest, False) is True:
            return name
    if path in CLI_COMMAND_NAMES:
        return CLI_COMMAND_NAMES[path]
    if path in routed:
        return command_name(routed[path][0])
    if len(path) == 1 and path[0] in covering:
        return command_name(covering[path[0]][0])
    return "_".join(path).replace("-", "_")

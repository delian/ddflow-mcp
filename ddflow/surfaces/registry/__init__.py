"""The declarative command registry: one description of a command, both surfaces generated.

A `Command` names an operation once -- its CLI path, its MCP tool, its parameters, the
function that runs it and the shape both surfaces print -- and this package generates what
used to be written twice by hand: the argparse subparser (`add_commands`), the MCP tool's
JSON Schema (`input_schema`) and the tool-table entry the MCP engine dispatches on
(`Command.tool_entry`). D-unify 4 (strangler): commands migrate in slices; until one
does, the hand-written parser and `TOOLS` entry stay the source of truth, and
`tests/test_command_registry.py` proves that a `Command` declared to match one generates
the same argparse help and the same `tools/list` schema.

Standard library only. Nothing here imports a surface, so `mcp.py` and the parsers can
both depend on it.

The declarations and helpers are split across the submodules below and re-exported here;
`SuggestingParser` and its `_STDLIB_DROPS_SUBCOMMAND_DASHDASH` flag stay in this module so
the ``monkeypatch.setattr(R, "_STDLIB_DROPS_SUBCOMMAND_DASHDASH", ...)`` tests keep reaching
the value `_get_values` reads.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import re
import sys
from typing import Any

from ..argtypes import glob_list_hint
from ._aliases import (
    KINDS,
    Alias,
    AliasChoices,
    Notices,
    _did_you_mean,
    add_command_alias,
    closest,
    used_aliases,
)
from ._assemble import add_commands, by_tool
from ._command import (
    GROUP_DEPTH,
    MAX_VIA,
    CliPolicy,
    Command,
)
from ._command_names import (
    CLI_COMMAND_NAMES,
    CLI_FLAG_COMMANDS,
    command_for_path,
)
from ._derived import (
    covering_tools,
    declared_words,
    exempt_paths,
    flag_exemptions,
    prose_reasons,
    routed_paths,
)
from ._mcp_aliases import (
    rename_args,
    resolve_tool,
    unknown_arg_hint,
    unknown_tool_hint,
)
from ._param import (
    AS_AGENT,
    AS_AGENT_SPEC,
    OPTIONAL_NARGS,
    TYPES,
    Param,
    params_schema,
    properties_schema,
)
from ._schemas import (
    ARRAY_PAYLOADS,
    REFUSAL_KEY,
    SCHEMA_KEY,
    SCHEMA_VERSIONS,
    SHAPES,
    TOOL_PREFIX,
    array_schema,
    command_name,
    output_schema,
    payload_fields,
    result_schemas,
    result_shape,
    schema_tag,
    schema_version,
    tag_body,
)


def _stdlib_drops_subcommand_dashdash() -> bool:
    """Does this Python's argparse accept ``prog a -- x`` for a subcommand ``x`` of ``a``?
    (3.13 does; 3.12.3 reports ``invalid choice: '--'``.) Asked of argparse itself, once,
    so a release that backports the fix is not stripped twice."""
    top = argparse.ArgumentParser(exit_on_error=False)
    a = top.add_subparsers(dest="a").add_parser("a")
    a.add_subparsers(dest="x").add_parser("x")
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            top.parse_args(["a", "--", "x"])
    except (argparse.ArgumentError, SystemExit):
        return False
    return True


#: See `_stdlib_drops_subcommand_dashdash`; `SuggestingParser._get_values` reads it.
_STDLIB_DROPS_SUBCOMMAND_DASHDASH = _stdlib_drops_subcommand_dashdash()


class SuggestingParser(argparse.ArgumentParser):
    """The root parser: an unknown command gets a 'did you mean'. Its subparsers inherit it,
    and every level records the command word taken (`_WordsRecorder`)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.register("action", "parsers", _WordsRecorder)
        self._argv: list[str] = []

    def parse_known_args(self, args=None, namespace=None):
        # Kept for `error`: it reads the words that were really parsed, not `sys.argv`.
        self._argv = list(sys.argv[1:] if args is None else args)
        return super().parse_known_args(None if args is None else self._argv, namespace)

    def _get_values(self, action: argparse.Action, arg_strings: list[str]) -> Any:
        # Before Python 3.13 argparse strips a `--` only for ordinary positionals: the
        # values of a subparsers action keep it at their head, and the command word check
        # then refuses `--` itself -- `ddflow task -- list` died with "invalid choice: '--'"
        # on 3.12.3 (B-dashdash-312). The marker means "what follows is positional", which is
        # all a command word is, so drop the one that opens a subcommand's values -- but only
        # where argparse has not already (a second strip would eat a literal `--`).
        if (
            not _STDLIB_DROPS_SUBCOMMAND_DASHDASH
            and action.nargs == argparse.PARSER
            and arg_strings[:1] == ["--"]
            and len(arg_strings) > 1
        ):
            arg_strings = arg_strings[1:]
        return super()._get_values(action, arg_strings)

    def error(self, message: str) -> Any:
        for a in self._actions:
            # only the command word: another option's bad value is not a command name
            if isinstance(a, argparse._SubParsersAction) and (
                m := re.match(rf"argument {re.escape(a.dest)}: invalid choice: '([^']*)'", message)
            ):
                message += _did_you_mean(m.group(1), list(a.choices))
        super().error(glob_list_hint(message, self._argv))


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


__all__ = (
    "ARRAY_PAYLOADS",
    "AS_AGENT",
    "AS_AGENT_SPEC",
    "CLI_COMMAND_NAMES",
    "CLI_FLAG_COMMANDS",
    "GROUP_DEPTH",
    "KINDS",
    "MAX_VIA",
    "OPTIONAL_NARGS",
    "REFUSAL_KEY",
    "SCHEMA_KEY",
    "SCHEMA_VERSIONS",
    "SHAPES",
    "TOOL_PREFIX",
    "TYPES",
    "WORDS",
    "Alias",
    "AliasChoices",
    "CliPolicy",
    "Command",
    "Notices",
    "Param",
    "SuggestingParser",
    "add_command_alias",
    "add_commands",
    "array_schema",
    "by_tool",
    "closest",
    "command_for_path",
    "command_name",
    "covering_tools",
    "declared_words",
    "exempt_paths",
    "flag_exemptions",
    "output_schema",
    "params_schema",
    "parsed_path",
    "payload_fields",
    "properties_schema",
    "prose_reasons",
    "rename_args",
    "resolve_tool",
    "result_schemas",
    "result_shape",
    "routed_paths",
    "schema_tag",
    "schema_version",
    "tag_body",
    "unknown_arg_hint",
    "unknown_tool_hint",
    "used_aliases",
)

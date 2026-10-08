"""Stale references in a project's files: the vocabulary a surface hands in, and the scan.

The command table (an argparse tree) and the MCP tool table are the surfaces'; this turns
them into the `services.compat_refs.Vocabulary` the scanner resolves names against, so a
surface needs only `ddflow.api` for it (D-unify layering).
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..services.compat_refs import (  # noqa: F401  -- the types a surface names
    Finding,
    Renamed,
    Vocabulary,
    report,
    rewrite,
    scan,
)
from ..services.migrations import refs as MR


def _renamed(alias: Any) -> Renamed:
    return Renamed(alias.new, alias.since, alias.removed_in)


def _walk(
    parser: argparse.ArgumentParser,
    prefix: tuple[str, ...],
    commands: set[tuple[str, ...]],
    aliases: dict[tuple[str, ...], Renamed],
) -> None:
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for word, sub in action.choices.items():
            path = (*prefix, word)
            commands.add(path)
            _walk(sub, path, commands, aliases)
        for word, alias in getattr(action, "_compat", {}).items():
            aliases[(*prefix, word)] = _renamed(alias)


def command_names(
    parser: argparse.ArgumentParser,
) -> tuple[frozenset[tuple[str, ...]], dict[tuple[str, ...], Renamed]]:
    """``(every command path, the old names that still work)`` of an argparse tree."""
    commands: set[tuple[str, ...]] = set()
    aliases: dict[tuple[str, ...], Renamed] = {}
    _walk(parser, (), commands, aliases)
    return frozenset(commands), aliases


def tool_names(
    tools: Mapping[str, Mapping[str, Any]],
) -> tuple[frozenset[str], dict[str, Renamed]]:
    """``(every tool name, the old names that still work)`` of the MCP tool table."""
    aliases = {
        alias.old: _renamed(alias) for spec in tools.values() for alias in spec.get("aliases", ())
    }
    return frozenset(tools), aliases


def vocabulary(
    parser: argparse.ArgumentParser, tools: Mapping[str, Mapping[str, Any]]
) -> Vocabulary:
    """The commands, tools and old names of a parser and a tool table."""
    commands, command_aliases = command_names(parser)
    tool_set, tool_aliases = tool_names(tools)
    return Vocabulary(
        commands=commands,
        tools=tool_set,
        command_aliases=command_aliases,
        tool_aliases=tool_aliases,
    )


def _vocabulary_of(
    parser: Callable[[], argparse.ArgumentParser] | None,
    tools: Mapping[str, Mapping[str, Any]] | None,
) -> Vocabulary | None:
    """The vocabulary of what a surface has loaded, or None when it has loaded neither.

    What a surface has loaded is what it can check: with the tool table the tool names, and
    with the CLI parser the command words too."""
    if parser is None and tools is None:
        return None
    commands, command_aliases = command_names(parser()) if parser is not None else (frozenset(), {})
    names, tool_aliases = tool_names(tools) if tools is not None else (frozenset(), {})
    return Vocabulary(
        commands=commands,
        tools=names,
        command_aliases=command_aliases,
        tool_aliases=tool_aliases,
        check_commands=parser is not None,
        check_tools=tools is not None,
    )


def stale_references(
    repo: Path,
    parser: Callable[[], argparse.ArgumentParser] | None,
    tools: Mapping[str, Mapping[str, Any]] | None,
) -> tuple[list[str], list[str]]:
    """``(problems, notes)`` for `doctor`: the stale references in the project's files.

    With neither a parser nor a tool table there is nothing to check against and nothing is
    reported."""
    vocab = _vocabulary_of(parser, tools)
    return report(scan(repo, vocab)) if vocab is not None else ([], [])


_registered: dict[str, Any] = {"parser": None, "tools": None}


def provide_upgrade_vocabulary(
    parser_factory: Callable[[], argparse.ArgumentParser] | None = None,
    tools: Mapping[str, Mapping[str, Any]] | None = None,
) -> None:
    """Hand the command and tool tables to the stale-references migration
    (`services.migrations.refs`), so `ddflow upgrade` finds the deprecated names ddflow wrote.

    Called where a surface registers its vocabulary: the CLI with its parser and the tool
    table, the MCP server with the tools only. A later call adds to an earlier one (the CLI
    loads the tool table too). The vocabulary is built when an upgrade asks for it, not here,
    because building the parser costs more than most commands need."""
    _registered["parser"] = parser_factory or _registered["parser"]
    _registered["tools"] = tools if tools is not None else _registered["tools"]
    MR.provide(lambda: _vocabulary_of(_registered["parser"], _registered["tools"]))

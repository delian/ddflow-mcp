"""Stale references in a project's files: the vocabulary a surface hands in, and the scan.

The command table (an argparse tree) and the MCP tool table are the surfaces'; this turns
them into the `services.compat_refs.Vocabulary` the scanner resolves names against, so a
surface needs only `ddflow.api` for it (D-unify layering).
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from typing import Any

from ..services.compat_refs import (  # noqa: F401  -- the types a surface names
    Finding,
    Renamed,
    Vocabulary,
    report,
    rewrite,
    scan,
)


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

"""Assembling commands onto the root subparsers.

`add_commands` registers `Command` declarations (groups, group aliases, handlers,
executors) and `by_tool` indexes the command list by MCP tool name.
"""

from __future__ import annotations

import argparse
import dataclasses
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from ._aliases import Alias, add_command_alias
from ._command import GROUP_DEPTH, Command


def _group_subparsers(
    subparsers: argparse._SubParsersAction, group: str
) -> argparse._SubParsersAction | None:
    """The subparsers action of a group parser already on ``subparsers``, or None."""
    parser = subparsers.choices.get(group)
    if parser is None:
        return None
    return next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)


def _with_handler(
    cmd: Command,
    handlers: Mapping[tuple[str, ...], Callable[..., Any]] | None,
    executor: Callable[[Command], Callable[..., Any]] | None,
) -> Command:
    """``cmd`` with the CLI function it declares none of: its row in ``handlers``, else the
    ``executor``'s for a command that has a ``render``."""
    if cmd.handler is not None:
        return cmd
    if handlers and cmd.path in handlers:
        return dataclasses.replace(cmd, handler=handlers[cmd.path])
    if executor and (cmd.render is not None or cmd.cli is not None):
        return dataclasses.replace(cmd, handler=executor(cmd))
    return cmd


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
        cmd = _with_handler(declared, handlers, executor)
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

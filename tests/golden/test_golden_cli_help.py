"""Golden: `ddflow --help` and every subcommand's `--help`, byte for byte but for one wrap.

One snapshot per command, so a diff names the command whose help moved. The usage
paragraph is recorded on one line: Python 3.13's argparse wraps a long usage line at
different points than 3.11's (mutually exclusive groups, subcommand choices), and nothing
else in the help differs between them. Every word of the usage is still pinned.
"""

from __future__ import annotations

import argparse
import re

import pytest
from goldenfix import _pinned_environment  # noqa: F401 -- fixtures

from ddflow.surfaces.cli import build_parser


def _commands() -> dict[str, argparse.ArgumentParser]:
    """Every parser reachable from the root, keyed by its command path. An alias shares
    its command's parser object, so it is listed once, under the name argparse prints."""
    found: dict[str, argparse.ArgumentParser] = {}

    def walk(parser: argparse.ArgumentParser, path: str) -> None:
        found[path] = parser
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                seen: set[int] = set()
                for name, sub in action.choices.items():
                    if id(sub) not in seen:
                        seen.add(id(sub))
                        walk(sub, f"{path} {name}")

    walk(build_parser(), "ddflow")
    return found


PARSERS = _commands()
COMMANDS = sorted(PARSERS)


def unwrap_usage(text: str) -> str:
    """The help with its leading `usage:` paragraph joined onto one line."""
    usage, sep, rest = text.partition("\n\n")
    return re.sub(r"\s+", " ", usage).strip() + sep + rest


def test_the_command_tree(snapshot):
    """Which commands exist: an added, removed or renamed command shows here first."""
    assert COMMANDS == snapshot


@pytest.mark.parametrize("command", COMMANDS)
def test_help(command, snapshot):
    assert unwrap_usage(PARSERS[command].format_help()) == snapshot

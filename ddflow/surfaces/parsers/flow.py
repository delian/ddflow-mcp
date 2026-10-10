"""`ddflow` subcommands: pull requests, versions, promotion and the branching flow.

Registered by `cli.build_parser`, in the order `ddflow --help` lists them. The commands are
declared once in `surfaces/declared/flow.py` and run on the executor (`surfaces/cliexec.py`)."""

from __future__ import annotations

import argparse

from .. import cliexec
from ..declared.flow import COMMANDS, GROUPS
from ..registry import add_commands


def register(s: argparse._SubParsersAction) -> None:
    """Add the flow subcommands to `s`, the root `ddflow` subparsers."""
    add_commands(s, COMMANDS, groups=GROUPS, executor=cliexec.handler)

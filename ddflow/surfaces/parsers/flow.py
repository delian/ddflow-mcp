"""`ddflow` subcommands: pull requests, versions, promotion and the branching flow.

Registered by `cli.build_parser`, in the order `ddflow --help` lists them. The commands are
declared once in `surfaces/declared/flow.py`."""

from __future__ import annotations

import argparse

from ..commands.flow import cmd_flow, cmd_pr, cmd_promote, cmd_version
from ..declared.flow import COMMANDS, GROUPS
from ..registry import add_commands

#: The handler of each command: one per group, which reads the leaf from the namespace.
_GROUP_HANDLERS = {"pr": cmd_pr, "version": cmd_version, "promote": cmd_promote, "flow": cmd_flow}


def register(s: argparse._SubParsersAction) -> None:
    """Add the flow subcommands to `s`, the root `ddflow` subparsers."""
    add_commands(
        s, COMMANDS, groups=GROUPS, handlers={c.path: _GROUP_HANDLERS[c.path[0]] for c in COMMANDS}
    )

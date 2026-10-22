"""`ddflow grant`: the operator's recorded permissions. Generated from
`surfaces/declared/grants.py`."""

from __future__ import annotations

import argparse

from .. import cliexec
from ..commands.grants import cmd_grant_add, cmd_grant_list
from ..declared import grants as declared
from ..registry import add_commands


def register(s: argparse._SubParsersAction) -> None:
    """Add `grant` to `s`, the root `ddflow` subparsers."""
    add_commands(
        s,
        declared.COMMANDS,
        groups={"grant": "record or list the operator's grants (extra review rounds)"},
        handlers={("grant", "add"): cmd_grant_add, ("grant", "list"): cmd_grant_list},
        executor=cliexec.handler,
    )

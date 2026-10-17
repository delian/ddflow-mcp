"""`ddflow chore`: commit chores; a person approves the chore list. Generated from
`surfaces/declared/chore.py`."""

from __future__ import annotations

import argparse

from .. import cliexec
from ..declared import chore as declared
from ..registry import add_commands


def register(s: argparse._SubParsersAction) -> None:
    """Add `chore` to `s`, the root `ddflow` subparsers."""
    add_commands(
        s,
        declared.COMMANDS,
        groups={"chore": "commit or approve chore files (no reviewer)"},
        executor=cliexec.handler,
    )

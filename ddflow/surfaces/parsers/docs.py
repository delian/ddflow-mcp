"""`ddflow docs`: the documentation check. Generated from `surfaces/declared/docs.py`."""

from __future__ import annotations

import argparse

from .. import cliexec
from ..declared.docs import COMMANDS
from ..registry import add_commands


def register(s: argparse._SubParsersAction) -> None:
    """Add `docs` to `s`, the root `ddflow` subparsers."""
    add_commands(s, COMMANDS, executor=cliexec.handler)

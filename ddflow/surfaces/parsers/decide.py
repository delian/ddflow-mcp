"""`ddflow decide`: close a decide task. Generated from `surfaces/declared/decide.py`."""

from __future__ import annotations

import argparse

from .. import cliexec
from ..declared.decide import COMMANDS
from ..registry import add_commands


def register(s: argparse._SubParsersAction) -> None:
    """Add `decide` to `s`, the root `ddflow` subparsers."""
    add_commands(s, COMMANDS, executor=cliexec.handler)

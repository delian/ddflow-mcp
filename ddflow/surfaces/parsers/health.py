"""`ddflow health`: the watchdog. Generated from its declaration (`surfaces/declared/health.py`).

No `handlers` map: the command declares a `render`, so `cliexec.handler` makes its CLI function.
"""

from __future__ import annotations

import argparse

from .. import cliexec
from ..declared.health import COMMANDS
from ..registry import add_commands


def register(s: argparse._SubParsersAction) -> None:
    """Add `health` to `s`, the root `ddflow` subparsers."""
    add_commands(s, COMMANDS, executor=cliexec.handler)

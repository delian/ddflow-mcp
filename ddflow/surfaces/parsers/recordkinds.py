"""`ddflow record`: any data-declared record kind. Generated from `surfaces/declared/recordkinds.py`."""

from __future__ import annotations

import argparse

from .. import cliexec
from ..declared.recordkinds import COMMANDS
from ..registry import add_commands


def register(s: argparse._SubParsersAction) -> None:
    """Add `record` to `s`, the root `ddflow` subparsers."""
    add_commands(s, COMMANDS, executor=cliexec.handler)

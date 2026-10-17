"""`ddflow report`: the completion report. Generated from `surfaces/declared/report.py`."""

from __future__ import annotations

import argparse

from .. import cliexec
from ..declared import report as declared
from ..registry import add_commands


def register(s: argparse._SubParsersAction) -> None:
    """Add `report` to `s`, the root `ddflow` subparsers."""
    add_commands(s, declared.COMMANDS, executor=cliexec.handler)

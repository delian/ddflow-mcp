"""`ddflow lanes`: the lane ledger. Generated from its declaration (`surfaces/declared/lanes.py`)."""

from __future__ import annotations

import argparse

from ..commands.lanes import cmd_lanes
from ..declared.lanes import COMMANDS
from ..registry import add_commands


def register(s: argparse._SubParsersAction) -> None:
    """Add `lanes` to `s`, the root `ddflow` subparsers."""
    add_commands(s, COMMANDS, handlers={("lanes",): cmd_lanes})

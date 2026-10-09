"""`ddflow` subcommands: the loop: next, claim, heartbeat, release, wait, approve, gate, complete, abandon, remove, block, unblock, merge.

Registered by `cli.build_parser`, in the order `ddflow --help` lists them. Every command is
generated from its declaration (`surfaces/declared/lifecycle.py`)."""

from __future__ import annotations

import argparse

from ..commands.gates import cmd_gate
from ..commands.lifecycle import (
    cmd_abandon,
    cmd_block,
    cmd_claim,
    cmd_complete,
    cmd_heartbeat,
    cmd_merge,
    cmd_next,
    cmd_release,
    cmd_remove,
    cmd_unblock,
    cmd_wait,
)
from ..declared.lifecycle import COMMANDS
from ..registry import add_commands

#: The CLI function of each declared command (`approve`'s lives in `cli.py`: see `register`).
HANDLERS = {
    ("next",): cmd_next,
    ("claim",): cmd_claim,
    ("heartbeat",): cmd_heartbeat,
    ("release",): cmd_release,
    ("wait",): cmd_wait,
    ("gate", "status"): cmd_gate,
    ("gate", "list"): cmd_gate,
    ("gate", "run"): cmd_gate,
    ("gate", "verify"): cmd_gate,
    ("gate", "record"): cmd_gate,
    ("gate", "skip"): cmd_gate,
    ("complete",): cmd_complete,
    ("abandon",): cmd_abandon,
    ("remove",): cmd_remove,
    ("block",): cmd_block,
    ("unblock",): cmd_unblock,
    ("merge",): cmd_merge,
}


def register(s: argparse._SubParsersAction) -> None:
    """Add the lifecycle subcommands to `s`, the root `ddflow` subparsers."""
    # Handlers that live in `cli.py`; imported at call time, when `cli` is loaded.
    from ..cli import cmd_approve

    add_commands(
        s,
        COMMANDS,
        groups={"gate": "run / record / inspect a gate"},
        handlers={**HANDLERS, ("approve",): cmd_approve},
    )

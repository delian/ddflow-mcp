"""`ddflow` subcommands: adding and reshaping work: init, phase, task, split, resolve, update.

Registered by `cli.build_parser`, in the order `ddflow --help` lists them. Every command is
generated from its declaration (`surfaces/declared/queue.py`)."""

from __future__ import annotations

import argparse

from ..commands.queue import cmd_phase_add, cmd_resolve, cmd_split, cmd_task_add
from ..commands.setup import cmd_init
from ..declared.queue import COMMANDS
from ..registry import add_commands

#: The CLI function of each declared command (`update`'s lives in `cli.py`: see `register`).
HANDLERS = {
    ("init",): cmd_init,
    ("phase", "add"): cmd_phase_add,
    ("task", "add"): cmd_task_add,
    ("split",): cmd_split,
    ("resolve",): cmd_resolve,
}


def register(s: argparse._SubParsersAction) -> None:
    """Add the queue subcommands to `s`, the root `ddflow` subparsers."""
    # Handlers that live in `cli.py`; imported at call time, when `cli` is loaded.
    from ..cli import cmd_item_update

    add_commands(
        s,
        COMMANDS,
        groups={"phase": "add a phase", "task": "add a task"},
        handlers={**HANDLERS, ("update",): cmd_item_update},
    )

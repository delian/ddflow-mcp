"""`ddflow` subcommands: brief, external sync, long-running jobs and memory (the last two declared
once in `declared/memory.py`).

Registered by `cli.build_parser`, in the order `ddflow --help` lists them."""

from __future__ import annotations

import argparse

from .. import cliexec
from ..commands.knowledge import cmd_job, cmd_memory
from ..commands.operations import cmd_external
from ..declared.lifecycle import BY_TOOL
from ..declared.memory import COMMANDS, GROUPS
from ..registry import add_commands


def register(s: argparse._SubParsersAction) -> None:
    """Add the jobs subcommands to `s`, the root `ddflow` subparsers."""
    add_commands(s, [BY_TOOL["ddflow_brief"]], executor=cliexec.handler)

    ex = s.add_parser("external", help="dependencies on items in sibling repositories")
    ex_s = ex.add_subparsers(dest="external_cmd", required=True)
    ex_s.add_parser(
        "sync", help="observe the sibling-repo items `needs` names; record what changed"
    ).set_defaults(fn=cmd_external)

    add_commands(
        s,
        COMMANDS,
        groups=GROUPS,
        handlers={c.path: cmd_job if c.path[0] == "job" else cmd_memory for c in COMMANDS},
    )

"""`ddflow` subcommands: brief, external sync, long-running jobs and memory (the last two declared
once in `declared/memory.py`).

Registered by `cli.build_parser`, in the order `ddflow --help` lists them."""

from __future__ import annotations

import argparse

from ...api import lifecycle as A_LIFECYCLE
from ..commands.knowledge import cmd_job, cmd_memory
from ..commands.lifecycle import cmd_brief
from ..commands.operations import cmd_external
from ..declared.memory import COMMANDS, GROUPS
from ..registry import add_commands


def register(s: argparse._SubParsersAction) -> None:
    """Add the jobs subcommands to `s`, the root `ddflow` subparsers."""
    br = s.add_parser("brief", help="budgeted session-start pack")
    br.add_argument("--item", default="")
    br.add_argument("--phase", default="")
    br.add_argument(
        "--check-recovery", action="store_true", default=A_LIFECYCLE.DEFAULT_CHECK_RECOVERY
    )
    br.set_defaults(fn=cmd_brief)

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

"""`ddflow` subcommands: replay, recover, progress, loops, cleanup, doctor, rebuild, render, board, show.

Registered by `cli.build_parser`, in the order `ddflow --help` lists them."""

from __future__ import annotations

import argparse

from ..commands.operations import cmd_cleanup
from ..commands.reporting import (
    cmd_board,
    cmd_doctor,
    cmd_rebuild,
    cmd_recover,
    cmd_render,
    cmd_replay,
    cmd_show,
)
from ..declared.reporting import COMMANDS
from ..declared.setup import BY_TOOL as SETUP_BY_TOOL
from ..registry import add_commands


def register(s: argparse._SubParsersAction) -> None:
    """Add the reporting subcommands to `s`, the root `ddflow` subparsers."""
    # Handlers that live in `cli.py`; imported at call time, when `cli` is loaded.
    from ..cli import cmd_loops, cmd_progress

    HANDLERS = {
        ("replay",): cmd_replay,
        ("recover",): cmd_recover,
        ("progress",): cmd_progress,
        ("loops",): cmd_loops,
        ("cleanup",): cmd_cleanup,
        ("rebuild",): cmd_rebuild,
        ("render",): cmd_render,
        ("board",): cmd_board,
        ("show",): cmd_show,
    }

    by = {c.path: c for c in COMMANDS}
    add_commands(
        s, [by[("replay",)], by[("recover",)], by[("progress",)], by[("loops",)]], handlers=HANDLERS
    )
    add_commands(s, [by[("cleanup",)]], handlers=HANDLERS)

    add_commands(s, [SETUP_BY_TOOL["ddflow_doctor"]], handlers={("doctor",): cmd_doctor})
    add_commands(
        s, [by[("rebuild",)], by[("render",)], by[("board",)], by[("show",)]], handlers=HANDLERS
    )

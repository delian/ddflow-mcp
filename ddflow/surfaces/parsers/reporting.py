"""`ddflow` subcommands: replay, recover, progress, loops, cleanup, doctor, rebuild, render, board, show.

Registered by `cli.build_parser`, in the order `ddflow --help` lists them."""

from __future__ import annotations

import argparse
import dataclasses

from .. import cliexec
from ..commands.operations import cmd_cleanup
from ..commands.reporting import (
    cmd_board,
    cmd_doctor,
    cmd_render,
    cmd_replay,
    cmd_show,
    render_rebuild,
    render_recover,
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
        ("progress",): cmd_progress,
        ("loops",): cmd_loops,
        ("cleanup",): cmd_cleanup,
        ("render",): cmd_render,
        ("board",): cmd_board,
        ("show",): cmd_show,
    }

    by = {c.path: c for c in COMMANDS}
    # The prose is rendered here, not in the declaration: `declared/` is imported by the MCP
    # engine, which must import without the view layer's template engine.
    for path, render in ((("recover",), render_recover), (("rebuild",), render_rebuild)):
        by[path] = dataclasses.replace(by[path], render=render)
    add_commands(
        s,
        [by[("replay",)], by[("recover",)], by[("progress",)], by[("loops",)]],
        handlers=HANDLERS,
        executor=cliexec.handler,
    )
    add_commands(s, [by[("cleanup",)]], handlers=HANDLERS)

    add_commands(s, [SETUP_BY_TOOL["ddflow_doctor"]], handlers={("doctor",): cmd_doctor})
    add_commands(
        s,
        [by[("rebuild",)], by[("render",)], by[("board",)], by[("show",)]],
        handlers=HANDLERS,
        executor=cliexec.handler,
    )

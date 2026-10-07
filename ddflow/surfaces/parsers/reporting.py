"""`ddflow` subcommands: replay, recover, progress, loops, cleanup, doctor, rebuild, render, board, show.

Registered by `cli.build_parser`, in the order `ddflow --help` lists them."""

from __future__ import annotations

import argparse

from ...api import reporting as A_REPORTING
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


def register(s: argparse._SubParsersAction) -> None:
    """Add the reporting subcommands to `s`, the root `ddflow` subparsers."""
    # Handlers that live in `cli.py`; imported at call time, when `cli` is loaded.
    from ..cli import cmd_loops, cmd_progress

    rp = s.add_parser("replay", help="reconstruct the decision history from the log")
    rp.add_argument("--out", default="")
    rp.add_argument("--verify", action="store_true")
    rp.set_defaults(fn=cmd_replay)

    rc = s.add_parser("recover", help="find crashed agents' work (exit 2 = nothing)")
    rc.add_argument("--item", default="")
    rc.add_argument("--apply", action="store_true")
    rc.set_defaults(fn=cmd_recover)

    pg = s.add_parser("progress", help="work actually done, aggregated from the log")
    pg.add_argument("id", nargs="?", default="")
    pg.set_defaults(fn=cmd_progress)

    lp = s.add_parser("loops", help="circular references and runtime loops (exit 2 = none)")
    lp.set_defaults(fn=cmd_loops)

    cu = s.add_parser(
        "cleanup", help="classify ddflow worktrees/branches; --apply lands the safe ones"
    )
    cu.add_argument("--apply", action="store_true")
    cu.set_defaults(fn=cmd_cleanup)

    dr = s.add_parser("doctor", help="integrity + health check")
    dr.add_argument(
        "--upgrade",
        action="store_true",
        help="the upgrade plan instead (the same as `ddflow upgrade`)",
    )
    dr.set_defaults(fn=cmd_doctor)
    s.add_parser("rebuild", help="re-derive the index from the log").set_defaults(fn=cmd_rebuild)

    rn = s.add_parser("render", help="regenerate the human-readable views")
    rn.add_argument("--out", default=A_REPORTING.DEFAULT_RENDER_DIR)
    rn.add_argument(
        "--show",
        default="",
        help="print ONE view to stdout instead of writing files: lessons, lessons-summary, research, board",
    )
    rn.set_defaults(fn=cmd_render)
    bd = s.add_parser("board")
    bd.add_argument("--phase", default="")
    bd.set_defaults(fn=cmd_board)
    sh = s.add_parser("show")
    sh.add_argument("id")
    sh.set_defaults(fn=cmd_show)

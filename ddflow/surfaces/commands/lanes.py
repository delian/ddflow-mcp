"""`ddflow lanes`: the CLI function. One frame is the declared command; `--watch` repeats it."""

from __future__ import annotations

import argparse
import sys
from typing import Any

from .. import cliexec
from ..declared.lanes import COMMANDS
from ..tools._common import _api

CLEAR = "\x1b[H\x1b[2J"


def cmd_lanes(ns: argparse.Namespace, ctx: Any) -> int:
    """Print the ledger once, or with ``--watch`` redraw it until interrupted."""
    cmd = COMMANDS[0]
    if not getattr(ns, "watch", False):
        return cliexec.run(cmd, ns, ctx)
    interval = max(1, int(ctx.cfg.lanes.watch_interval_s))

    def draw() -> int:
        if sys.stdout.isatty():
            sys.stdout.write(CLEAR)
        code = cliexec.run(cmd, ns, ctx)
        sys.stdout.flush()
        return code

    return _api().lanes_watch(draw, lambda: ctx.log.mark(clock=False), interval_s=interval)

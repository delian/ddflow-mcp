"""A reader that closes the pipe (`ddflow list task | head -1`) ends the command quietly."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable

#: The exit of a command whose reader closed the pipe: shell convention for SIGPIPE,
#: 128 + 13. Not one of ddflow's own codes, which say what the COMMAND decided.
PIPE_CLOSED = 141


def run_quietly(run: Callable[[list[str] | None], int], argv: list[str] | None) -> int:
    """``run(argv)``, with a closed stdout turned into `PIPE_CLOSED` and no traceback.

    Buffered output meets a closed pipe at the flush, not in a print, so the flush is made
    here. The descriptor is then pointed at /dev/null (the recipe the Python docs give) so
    the flush at interpreter exit cannot fail again; the entry points exit right after.
    """
    try:
        code = run(argv)
        sys.stdout.flush()
        return code
    except BrokenPipeError:
        try:
            null = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(null, sys.stdout.fileno())
            finally:
                os.close(null)
        except (OSError, ValueError):
            pass
        return PIPE_CLOSED

"""Compatibility re-export: the Proc family moved to `ddflow.adapters.proc` + `ddflow.adapters.proc_server`.

This shim stays until its import sites import the adapter modules directly, then it is deleted.
The private `_shell_group` seam is re-exported explicitly because the proc tests patch it.
"""

from ddflow.adapters.proc import *  # noqa: F401,F403  # the port + run/capture/spawn_shell + Captured + TIMEOUTS
from ddflow.adapters.proc_server import *  # noqa: F401,F403  # run_shell, kill_group, relay_stdio, ...
from ddflow.adapters.proc_server import (  # noqa: F401  # private seams the tests patch
    _WATCHER,
    _arm_watcher,
    _drain,
    _feed_stdin,
    _note_cost,
    _shell_group,
    _start_watcher,
    _text,
)

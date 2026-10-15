"""The `plan` command group: the declared `plan check` (B-gap-plan-lint).

No `handlers` map: the command declares a `render`, so `cliexec.handler` makes its CLI
function and nothing here has to write one (the same route `viewers_lists` uses).
"""

from __future__ import annotations

import argparse

from .. import cliexec
from ..declared import plan as declared
from ..registry import add_commands


def register(s: argparse._SubParsersAction) -> None:
    """Add the `plan` group and its commands to `s`, the root `ddflow` subparsers."""
    add_commands(
        s,
        declared.COMMANDS,
        groups={"plan": "check a plan before filing it"},
        executor=cliexec.handler,
    )

"""CLI half of a declared family that has no parser module of its own.

Registration by convention (DX-registry-files-shared): a new command family is a file in
`surfaces/declared/` with a module-level ``COMMANDS``; when no `surfaces/parsers/<name>.py`
exists for its first word, the command is added here, so no parser registry names it either.
"""

from __future__ import annotations

import argparse

from .. import cliexec
from ..declared import family_modules
from ..registry import add_commands


def register(s: argparse._SubParsersAction) -> None:
    """Add every declared command whose first word no parser module has taken."""
    for module in family_modules():
        fresh = tuple(c for c in module.COMMANDS if c.path and c.path[0] not in s.choices)
        if fresh:
            add_commands(s, fresh, executor=cliexec.handler)

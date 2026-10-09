"""`ddflow` subcommands: reviewers, review and adopt.

Registered by `cli.build_parser`, in the order `ddflow --help` lists them. Reviewers and
review are generated from their declarations (`surfaces/declared/review.py`); `adopt` is
declared with the setup commands (`surfaces/declared/setup.py`)."""

from __future__ import annotations

import argparse

from ..commands.review import cmd_review, cmd_reviewers
from ..commands.setup import cmd_adopt
from ..declared.review import COMMANDS
from ..declared.setup import COMMANDS as SETUP_COMMANDS
from ..registry import add_commands

#: The CLI function of each declared command.
HANDLERS = {
    **{
        ("reviewers", v): cmd_reviewers
        for v in ("detect", "list", "presets", "add", "approve", "test")
    },
    ("review",): cmd_review,
}


def register(s: argparse._SubParsersAction) -> None:
    """Add the review subcommands to `s`, the root `ddflow` subparsers."""
    add_commands(
        s,
        [c for c in COMMANDS if c.path[:1] in (("reviewers",), ("review",))],
        groups={"reviewers": "find, list and test cross-family reviewers"},
        handlers=HANDLERS,
    )

    add_commands(
        s,
        [c for c in SETUP_COMMANDS if c.path == ("adopt",)],
        handlers={("adopt",): cmd_adopt},
    )

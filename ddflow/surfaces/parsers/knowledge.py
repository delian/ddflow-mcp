"""`ddflow` subcommands: lessons, recall, similar, dupes, links and decisions.

Registered by `cli.build_parser`, in the order `ddflow --help` lists them. Every command is
generated from its declaration (`surfaces/declared/knowledge.py`), `link` included: its relation
flags are a required, mutually exclusive group of CLI-only parameters. A command that declares
a `render` runs on the CLI executor; the others are named in `HANDLERS`."""

from __future__ import annotations

import argparse

from .. import cliexec
from ..commands.decisions import cmd_decision
from ..commands.knowledge import cmd_lesson, cmd_recall
from ..declared.knowledge import COMMANDS
from ..registry import add_commands

#: The CLI function of each declared command.
HANDLERS = {
    ("lesson", "add"): cmd_lesson,
    ("lesson", "verify"): cmd_lesson,
    ("recall",): cmd_recall,
    ("decision", "add"): cmd_decision,
    ("decision", "list"): cmd_decision,
    ("decision", "search"): cmd_decision,
}


def register(s: argparse._SubParsersAction) -> None:
    """Add the knowledge subcommands to `s`, the root `ddflow` subparsers."""
    first = ("lesson", "recall", "similar", "dupes", "link")
    add_commands(
        s,
        [c for c in COMMANDS if c.path[:1] and c.path[0] in first],
        handlers=HANDLERS,
        executor=cliexec.handler,
    )
    add_commands(
        s,
        [c for c in COMMANDS if c.path[:1] == ("decision",)],
        groups={"decision": "architectural decisions: record and consult"},
        handlers=HANDLERS,
        executor=cliexec.handler,
    )
    # `ddflow decision` alone lists them.
    dc = s.choices["decision"]
    next(a for a in dc._actions if isinstance(a, argparse._SubParsersAction)).required = False
    dc.set_defaults(fn=cmd_decision, decision_cmd="list", all=False)

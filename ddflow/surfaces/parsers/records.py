"""`ddflow` subcommands: status, research, bugs and sessions.

Registered by `cli.build_parser`, in the order `ddflow --help` lists them. Research, bugs
and sessions are generated from their declarations (`surfaces/declared/records.py`); the
session views (`list`, `show`) join the `session` group from `commands/viewers_sessions.py`.
"""

from __future__ import annotations

import argparse

from ..commands.bug_reopen import cmd_bug_reopen
from ..commands.knowledge import cmd_bug, cmd_research, cmd_session
from ..commands.reporting import cmd_status
from ..commands.viewers_sessions import COMMANDS as SESSION_VIEWS
from ..declared.records import COMMANDS
from ..registry import add_commands

#: The CLI function of each declared command.
HANDLERS = {
    ("research",): cmd_research,
    ("bug", "found"): cmd_bug,
    ("bug", "file-tasks"): cmd_bug,
    ("bug", "fixed"): cmd_bug,
    ("bug", "invalid"): cmd_bug,
    ("bug", "reopen"): cmd_bug_reopen,
    ("session", "start"): cmd_session,
    ("session", "prompt"): cmd_session,
    ("session", "note"): cmd_session,
    ("session", "adopt-orphans"): cmd_session,
    ("session", "end"): cmd_session,
}


def register(s: argparse._SubParsersAction) -> None:
    """Add the records subcommands to `s`, the root `ddflow` subparsers."""
    stt = s.add_parser("status", help="one answer to 'what is the state of this project?'")
    stt.set_defaults(fn=cmd_status)
    add_commands(s, [*COMMANDS, *SESSION_VIEWS], handlers=HANDLERS)

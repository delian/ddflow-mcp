"""`ddflow` subcommands: project rules, verify, ci and onboard.

Registered by `cli.build_parser`, in the order `ddflow --help` lists them."""

from __future__ import annotations

import argparse

from ..commands.ci import cmd_ci
from ..commands.onboard import add_onboard_parser
from ..commands.rule_sync import cmd_rule_sync
from ..commands.rules import cmd_rule
from ..commands.verify import cmd_verify
from ..declared.export import BY_TOOL as _EXPORT_BY_TOOL
from ..declared.review import BY_TOOL as _REVIEW_BY_TOOL
from ..declared.rules import COMMANDS
from ..registry import add_commands

#: The CLI function of each declared command.
HANDLERS = {
    ("rule", "add"): cmd_rule,
    ("rule", "edit"): cmd_rule,
    ("rule", "list"): cmd_rule,
    ("rule", "search"): cmd_rule,
    ("rule", "show"): cmd_rule,
    ("rule", "remove"): cmd_rule,
    ("rule", "sync"): cmd_rule_sync,
}


def register(s: argparse._SubParsersAction) -> None:
    """Add the rules subcommands to `s`, the root `ddflow` subparsers."""
    add_commands(
        s,
        COMMANDS,
        groups={"rule": "project rules: add, edit, list, search, show, remove"},
        handlers=HANDLERS,
    )
    # `ddflow rule` alone lists them.
    ru = s.choices["rule"]
    next(a for a in ru._actions if isinstance(a, argparse._SubParsersAction)).required = False
    ru.set_defaults(fn=cmd_rule, rule_cmd="list", tag="", scope="")

    add_commands(s, [_REVIEW_BY_TOOL["ddflow_verify"]], handlers={("verify",): cmd_verify})
    add_commands(s, [_EXPORT_BY_TOOL["ddflow_ci"]], handlers={("ci",): cmd_ci})
    add_onboard_parser(s)

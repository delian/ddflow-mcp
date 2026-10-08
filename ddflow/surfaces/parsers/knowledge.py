"""`ddflow` subcommands: lessons, recall, similar, dupes, links and decisions.

Registered by `cli.build_parser`, in the order `ddflow --help` lists them. Every command but
`link` is generated from its declaration (`surfaces/declared/knowledge.py`); `link` names
the relation with one flag each, in a mutually exclusive group, which a declaration does not
express."""

from __future__ import annotations

import argparse

from ...core.model import LINK_RELATIONS
from ..commands.decisions import cmd_decision
from ..commands.knowledge import cmd_dupes, cmd_lesson, cmd_link, cmd_recall, cmd_similar
from ..declared.knowledge import COMMANDS
from ..registry import add_commands

#: The CLI function of each declared command.
HANDLERS = {
    ("lesson", "add"): cmd_lesson,
    ("lesson", "verify"): cmd_lesson,
    ("lesson", "search"): cmd_lesson,
    ("recall",): cmd_recall,
    ("similar",): cmd_similar,
    ("dupes",): cmd_dupes,
    ("decision", "add"): cmd_decision,
    ("decision", "list"): cmd_decision,
    ("decision", "show"): cmd_decision,
    ("decision", "search"): cmd_decision,
    ("decision", "applicable"): cmd_decision,
    ("decision", "supersede"): cmd_decision,
}


def register(s: argparse._SubParsersAction) -> None:
    """Add the knowledge subcommands to `s`, the root `ddflow` subparsers."""
    first = ("lesson", "recall", "similar", "dupes")
    add_commands(s, [c for c in COMMANDS if c.path[:1] and c.path[0] in first], handlers=HANDLERS)
    _add_link(s)
    add_commands(
        s,
        [c for c in COMMANDS if c.path[:1] == ("decision",)],
        groups={"decision": "architectural decisions: record and consult"},
        handlers=HANDLERS,
    )
    # `ddflow decision` alone lists them.
    dc = s.choices["decision"]
    next(a for a in dc._actions if isinstance(a, argparse._SubParsersAction)).required = False
    dc.set_defaults(fn=cmd_decision, decision_cmd="list", all=False)


def _add_link(s: argparse._SubParsersAction) -> None:
    lk = s.add_parser(
        "link",
        help="settle a near-duplicate pair: say how one record relates to another",
    )
    lk.add_argument("subject", help="the record being related (the duplicate, for a merge)")
    # One flag per relation, DECLARED from LINK_RELATIONS, so the parser's accepted set,
    # the API's and `cmd_link`'s lookup are one list: a relation added to the model cannot
    # be accepted over MCP and rejected by argparse (`--duplicate_of` -> the flag
    # `--duplicate-of`, argparse's own dest rule). Help text is cosmetic and falls back.
    _link_help = {
        "extends": "subject adds to ID",
        "duplicate_of": "subject is the same thing as ID",
        "related": "subject is related to ID",
        "distinct": "subject is NOT a duplicate of ID: dismiss the pair for good",
    }
    lg = lk.add_mutually_exclusive_group(required=True)
    for _rel in LINK_RELATIONS:
        lg.add_argument(
            f"--{_rel.replace('_', '-')}",
            metavar="ID",
            default="",
            dest=_rel,
            # `.get`, not `[...]`: a relation added to the model then still parses (with a
            # generic help), instead of `build_parser()` raising KeyError on every command.
            help=_link_help.get(_rel, f"subject {_rel.replace('_', ' ')} ID"),
        )
    lk.add_argument("--reason", default="", help="why, recorded with the link")
    lk.set_defaults(fn=cmd_link)

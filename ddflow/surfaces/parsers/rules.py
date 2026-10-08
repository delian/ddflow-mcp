"""`ddflow` subcommands: project rules, verify, ci and onboard.

Registered by `cli.build_parser`, in the order `ddflow --help` lists them."""

from __future__ import annotations

import argparse

from ..commands.ci import add_ci_parser
from ..commands.onboard import add_onboard_parser
from ..commands.rule_sync import cmd_rule_sync
from ..commands.rules import cmd_rule
from ..commands.verify import add_verify_parser


def register(s: argparse._SubParsersAction) -> None:
    """Add the rules subcommands to `s`, the root `ddflow` subparsers."""
    ru = s.add_parser("rule", help="project rules: add, edit, list, search, show, remove")
    ru_s = ru.add_subparsers(dest="rule_cmd", required=False)
    rua = ru_s.add_parser("add")
    rua.add_argument("--id", required=True)
    rua.add_argument("--title", required=True)
    rua.add_argument("--content", default="")
    rua.add_argument("--tags", default="")
    rua.add_argument("--scope", default="project")
    rua.add_argument("--priority", type=int, default=None)
    rua.add_argument("--globs", default="")
    g = rua.add_mutually_exclusive_group()
    g.add_argument("--new", action="store_true", help="a different rule, file it")
    g.add_argument("--extends", metavar="ID", default="")
    g.add_argument("--duplicate-of", dest="duplicate_of", metavar="ID", default="")
    g.add_argument("--related", metavar="ID", default="")
    g.add_argument("--check", action="store_true", help="dry run: list duplicates only")
    rua.set_defaults(fn=cmd_rule)
    rue = ru_s.add_parser("edit")
    rue.add_argument("id")
    for flag in ("title", "content", "tags", "scope", "globs"):
        rue.add_argument(f"--{flag}", default=None)
    rue.add_argument("--priority", type=int, default=None)
    # A new title or content is duplicate-checked against every other record kind
    # (D-rule-dedupe-everywhere); these answer it.
    ge = rue.add_mutually_exclusive_group()
    ge.add_argument("--new", action="store_true", help="answer the duplicate check: different")
    ge.add_argument("--related", metavar="ID", default="", help="answer it: related to ID")
    rue.set_defaults(fn=cmd_rule)
    rul = ru_s.add_parser("list")
    rul.add_argument("--tag", default="")
    rul.add_argument("--scope", default="")
    rul.set_defaults(fn=cmd_rule)
    rus = ru_s.add_parser("search")
    rus.add_argument("query")
    rus.add_argument("--limit", type=int, default=10)
    rus.add_argument("--exact", action="store_true")
    rus.add_argument("--regex", action="store_true")
    rus.add_argument("--tag", default="")
    rus.add_argument("--scope", default="")
    rus.set_defaults(fn=cmd_rule)
    rush = ru_s.add_parser("show")
    rush.add_argument("id")
    rush.set_defaults(fn=cmd_rule)
    rur = ru_s.add_parser("remove")
    rur.add_argument("id")
    rur.set_defaults(fn=cmd_rule)
    rusy = ru_s.add_parser(
        "sync",
        help="record hand-edited rule files in the log; write the files the log has and the disk lacks",
    )
    rusy.set_defaults(fn=cmd_rule_sync)
    ru.set_defaults(fn=cmd_rule, rule_cmd="list", tag="", scope="")

    add_verify_parser(s)
    add_ci_parser(s)
    add_onboard_parser(s)

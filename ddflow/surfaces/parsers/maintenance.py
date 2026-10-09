"""`ddflow` subcommands: config, export, bisect, cadence, pins, tests, precommit.

Registered by `cli.build_parser`, in the order `ddflow --help` lists them."""

from __future__ import annotations

import argparse

from ..commands.bisect import cmd_bisect
from ..commands.export import cmd_export
from ..commands.operations import cmd_cadence, cmd_pins, cmd_precommit, cmd_tests
from ..commands.setup import cmd_config, cmd_upgrade
from ..declared.export import BY_TOOL as EXPORT_BY_TOOL
from ..declared.setup import COMMANDS as SETUP_COMMANDS
from ..registry import add_commands


def register(s: argparse._SubParsersAction) -> None:
    """Add the maintenance subcommands to `s`, the root `ddflow` subparsers."""
    add_commands(
        s,
        [c for c in SETUP_COMMANDS if c.path == ("config",)],
        handlers={("config",): cmd_config},
    )

    up = s.add_parser(
        "upgrade",
        help="what upgrading this project to the running ddflow would change "
        "(the plan; writes nothing; exit 0 up to date, 1 pending), or --apply it",
    )
    up.add_argument(
        "--plan",
        action="store_true",
        default=False,
        help="print the plan (the default: nothing is written)",
    )
    up.add_argument(
        "--apply",
        nargs="?",
        const="all",
        default=None,
        metavar="CATEGORIES",
        help="do what the plan lists, after saving what it will rewrite to .ddflow/backups/: "
        "all (the default), or a comma list of repairs, migrations, config, instructions, hooks, mcp, "
        "features. Exit 0 done, 1 a step failed, 2 a step could not run, 3 an item needs "
        "--confirm",
    )
    up.add_argument(
        "--confirm",
        action="append",
        metavar="KEY",
        help="with --apply: accept the change to this knob, file or repair that the operator "
        "set or wrote (repeatable; needs --reason, which is recorded)",
    )
    up.add_argument("--reason", default="", help="with --confirm: why the operator accepts it")
    up.add_argument(
        "--backup",
        default="",
        metavar="MODE",
        help="with --apply: where the originals go: local (default), snapshot or none",
    )
    up.add_argument(
        "--snapshot",
        action="store_true",
        default=False,
        help="with --apply: save the originals as a git snapshot (a tag on HEAD) instead of "
        "a local copy; the same as --backup snapshot",
    )
    up.add_argument(
        "--restore",
        nargs="?",
        const="latest",
        default=None,
        metavar="NAME",
        help="put back what a local backup or a snapshot holds (NAME, or the newest when "
        "omitted), saving what it replaces; stands alone: no other option may accompany it",
    )
    up.set_defaults(fn=cmd_upgrade)

    add_commands(
        s,
        [EXPORT_BY_TOOL["ddflow_export"], EXPORT_BY_TOOL["ddflow_bisect"]],
        handlers={("export",): cmd_export, ("bisect",): cmd_bisect},
    )

    cd = s.add_parser("cadence", help="which periodic passes are due (exit 2 = none)")
    cd.add_argument("--ran", default="")
    cd.add_argument("--note", default="")
    cd.set_defaults(fn=cmd_cadence)

    pn = s.add_parser(
        "pins",
        help="which text of an instruction file a test pins, before you compress it",
    )
    pn.add_argument("document", help="the instruction file, e.g. AGENTS.md")
    pn.add_argument("--tests", default="", help="comma-separated test dirs (default: tests,test)")
    pn.add_argument(
        "--min-needle",
        type=int,
        default=None,
        help="shortest literal that counts as a pin (default 12)",
    )
    pn.add_argument("--top", type=int, default=10, help="how many free stretches to show")
    pn.set_defaults(fn=cmd_pins)

    add_commands(
        s,
        [EXPORT_BY_TOOL["ddflow_tests"], EXPORT_BY_TOOL["ddflow_precommit"]],
        handlers={("tests",): cmd_tests, ("precommit",): cmd_precommit},
    )

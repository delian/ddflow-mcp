"""`ddflow` subcommands: config, export, bisect, cadence, pins, tests, precommit.

Registered by `cli.build_parser`, in the order `ddflow --help` lists them."""

from __future__ import annotations

import argparse

from .. import cliexec
from ..commands.export import cmd_export
from ..commands.operations import cmd_cadence, cmd_pins, cmd_precommit
from ..commands.setup import cmd_config, cmd_upgrade
from ..declared.cadence import COMMANDS as COMMANDS_CADENCE
from ..declared.export import BY_TOOL as EXPORT_BY_TOOL
from ..declared.mergequeue import COMMANDS as COMMANDS_MERGEQUEUE
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
        "all (the default), or a comma list of repairs, migrations, records, config, instructions, "
        "hooks, mcp, features. Exit 0 done, 1 a step failed, 2 a step could not run, 3 an item needs "
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
    up.add_argument(
        "--check",
        action="store_true",
        default=False,
        help="ask the package index now whether a newer ddflow exists and say the running "
        "version, the newest, this project's stamp and how to upgrade (exit 0 newest, "
        "1 a newer release, 2 off or unreachable); stands alone",
    )
    up.add_argument(
        "--status",
        action="store_true",
        default=False,
        help="where the upgrade stands: running version, newest release and when it was asked, "
        "this project's stamp, the plan with each step's state (pending, running, done, failed, unavailable, "
        "needs operator confirmation, waived), the backups, and the agents and MCP servers last "
        "seen on an older version (exit 0 up to date, 1 steps pending or a run interrupted); "
        "stands alone",
    )
    up.add_argument(
        "--history",
        action="store_true",
        default=False,
        help="list the upgrades this project's log records, oldest first; stands alone",
    )
    up.add_argument(
        "--resume",
        action="store_true",
        default=False,
        help="finish an --apply that was interrupted, without repeating the steps it settled "
        "(--confirm/--reason/--backup go with it; exit 2 when none was interrupted)",
    )
    up.add_argument(
        "--self",
        action="store_true",
        default=False,
        help="upgrade ddflow ITSELF with the tool that installed it (uv tool, pipx, a virtualenv's "
        "pip); where it cannot (uvx, a container image, a source checkout, a read-only install) "
        "it prints the exact command and exits 2. Needs the operator's consent: --yes --reason "
        "([upgrade].self); stands alone",
    )
    up.add_argument("--to", default="", metavar="VERSION", help="with --self: install this version")
    up.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="with --self: name the command and run nothing",
    )
    up.add_argument(
        "--rollback",
        action="store_true",
        default=False,
        help="with --self: reinstall the version that ran before the last --self",
    )
    up.add_argument(
        "--yes",
        action="store_true",
        default=False,
        help="with --self: the operator consents to installing a new ddflow (needs --reason)",
    )
    up.set_defaults(fn=cmd_upgrade)

    add_commands(
        s,
        [EXPORT_BY_TOOL["ddflow_export"], EXPORT_BY_TOOL["ddflow_bisect"]],
        handlers={("export",): cmd_export},
        executor=cliexec.handler,
    )

    add_commands(s, COMMANDS_CADENCE, handlers={("cadence",): cmd_cadence, ("pins",): cmd_pins})
    add_commands(s, COMMANDS_MERGEQUEUE, executor=cliexec.handler)

    add_commands(
        s,
        [EXPORT_BY_TOOL["ddflow_tests"], EXPORT_BY_TOOL["ddflow_precommit"]],
        handlers={("precommit",): cmd_precommit},
        executor=cliexec.handler,
    )

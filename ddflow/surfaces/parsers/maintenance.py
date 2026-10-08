"""`ddflow` subcommands: config, export, bisect, cadence, pins, tests, precommit.

Registered by `cli.build_parser`, in the order `ddflow --help` lists them."""

from __future__ import annotations

import argparse

from ..commands.bisect import add_bisect_parser
from ..commands.export import add_export_parser
from ..commands.operations import cmd_cadence, cmd_pins, cmd_precommit, cmd_tests
from ..commands.setup import cmd_config, cmd_upgrade


def register(s: argparse._SubParsersAction) -> None:
    """Add the maintenance subcommands to `s`, the root `ddflow` subparsers."""
    cf = s.add_parser("config", help="print every knob, its value and its source")
    cf.add_argument("--explain", action="store_true")
    cf.add_argument("--filter", default="")
    cf.add_argument(
        "--set",
        default="",
        help="edit one key in place, e.g. --set gate.unit_tests.command 'pytest -q'",
    )
    cf.add_argument(
        "value",
        nargs="*",
        default=[],
        help="the value, when --set is used; or `KEY VALUE` with no --set "
        "(ddflow config review.max_rounds 0 --local)",
    )
    cf.add_argument(
        "--append-toml",
        default="",
        help="append this TOML to .ddflow/config.toml (validated first)",
    )
    cf.add_argument(
        "--local",
        action="store_true",
        help="write --set/--append-toml to the git-ignored .ddflow/local/config.toml: "
        "this machine's endpoints, hosts, key variables and sizing, never committed",
    )
    cf.set_defaults(fn=cmd_config)

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
        "all (the default), or a comma list of repairs, config, instructions, hooks, mcp, "
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
        help="with --apply: where the originals go (local, none); default [upgrade].backup",
    )
    up.set_defaults(fn=cmd_upgrade)

    add_export_parser(s)
    add_bisect_parser(s)

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

    ts = s.add_parser(
        "tests",
        help="the tests your change reaches, and a parallel command to run them (exit 2 = none)",
    )
    ts.add_argument("--item", default="", help="an item id: use its worktree and base")
    ts.add_argument("--base", default="", help="compare against this ref (default: the base)")
    ts.set_defaults(fn=cmd_tests)

    pc = s.add_parser(
        "precommit",
        help="propose a .pre-commit-config.yaml for this repository's stacks "
        "(writes nothing without --write)",
    )
    pc.add_argument(
        "--ddflow-cmd",
        default="ddflow",
        help="how the proposed local hooks reach ddflow (default: `ddflow` on PATH)",
    )
    pc.add_argument(
        "--write",
        action="store_true",
        help="create .pre-commit-config.yaml; an existing one is never replaced (exit 3)",
    )
    pc.set_defaults(fn=cmd_precommit)

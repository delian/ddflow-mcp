"""`ddflow` subcommands: reviewers, review and adopt.

Registered by `cli.build_parser`, in the order `ddflow --help` lists them. Reviewers and
review are generated from their declarations (`surfaces/declared/review.py`); `adopt` is
declared with the setup commands."""

from __future__ import annotations

import argparse

from ...services.adopt import AGENT_TARGETS
from ..commands.review import cmd_review, cmd_reviewers
from ..commands.setup import cmd_adopt
from ..declared.review import COMMANDS
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

    ad = s.add_parser("adopt", help="install ddflow into this project for one or more agents")
    ad.add_argument(
        "--agents",
        default="",
        # GENERATED from the registry, never typed. A hand-kept list here drifted the
        # moment `cursor` was added, and `test_every_supported_agent_is_named_where_a_user
        # _would_look` exists because of it: a capability nobody can find is one nobody
        # uses. Generating it means adding an agent cannot leave this behind.
        help=f"comma-separated: {','.join(AGENT_TARGETS)} (default: all)",
    )
    ad.add_argument("--docs", default="docs/ddflow", help="where to write the drivers")
    ad.add_argument(
        "--launch",
        default="auto",
        choices=["auto", "uvx", "docker", "python"],
        help="how agents spawn the MCP server: 'auto' prefers uvx for an install from a "
        "package index and this installation otherwise; 'docker' needs no Python "
        "toolchain at all",
    )
    ad.add_argument(
        "--image",
        default="ghcr.io/OWNER/ddflow:latest",
        help="container image used by --launch docker",
    )
    ad.add_argument(
        "--refresh-docs",
        action="store_true",
        help="rewrite ONLY the driver docs, the AGENTS.md/CLAUDE.md blocks and the agents' "
        "native rules from this ddflow's templates; leaves MCP launches, hooks and command "
        "files alone (what `doctor` points to when drivers lag)",
    )
    ad.set_defaults(fn=cmd_adopt)

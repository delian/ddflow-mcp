"""`ddflow` subcommands: reviewers, review and adopt.

Registered by `cli.build_parser`, in the order `ddflow --help` lists them."""

from __future__ import annotations

import argparse

from ...services.adopt import AGENT_TARGETS
from ..commands.review import cmd_review, cmd_reviewers
from ..commands.setup import cmd_adopt


def register(s: argparse._SubParsersAction) -> None:
    """Add the review subcommands to `s`, the root `ddflow` subparsers."""
    rv = s.add_parser("reviewers", help="find, list and test cross-family reviewers")
    rv_s = rv.add_subparsers(dest="reviewers_cmd", required=True)
    rvd = rv_s.add_parser("detect", help="probe well-known local endpoints")
    rvd.add_argument(
        "--write",
        action="store_true",
        help="append the discovered reviewers to the git-ignored .ddflow/local/reviewers.toml",
    )
    rvd.add_argument(
        "--shared",
        action="store_true",
        help="with --write: commit them to .ddflow/config.toml instead, for every clone",
    )
    rvd.set_defaults(fn=cmd_reviewers)
    rv_s.add_parser("list").set_defaults(fn=cmd_reviewers)
    rv_s.add_parser("presets", help="ready-made provider settings").set_defaults(fn=cmd_reviewers)
    rva = rv_s.add_parser("add", help="add a reviewer from a preset")
    rva.add_argument("--preset", default="", help="see `ddflow reviewers presets`")
    rva.add_argument("--name", default="")
    rva.add_argument("--model", default="")
    rva.add_argument("--base-url", default="")
    rva.add_argument("--gates", default="")
    rva.add_argument(
        "--shared",
        action="store_true",
        help="write to the committed .ddflow/config.toml instead of the git-ignored "
        ".ddflow/local/reviewers.toml -- only for a reviewer every clone should use",
    )
    rva.add_argument(
        "--no-launch",
        action="store_true",
        help="do not auto-start a local server for this reviewer",
    )
    rva.set_defaults(fn=cmd_reviewers)
    rvp = rv_s.add_parser(
        "approve",
        help="a PERSON vouches for a tool-written reviewer entry (refused under an agent "
        "identity); with no name, list the entries waiting (anyone may)",
    )
    rvp.add_argument("name", nargs="?", default="")
    rvp.add_argument("--note", default="")
    rvp.set_defaults(fn=cmd_reviewers)
    rvt = rv_s.add_parser("test", help="send a tiny known-buggy diff and check the reply")
    rvt.add_argument("name", nargs="?", default="")
    rvt.set_defaults(fn=cmd_reviewers)

    rw = s.add_parser(
        "review",
        help="run the configured reviewer(s) over an item's diff",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="two commands share this parser:\n"
        "  ddflow review <id> --gate G [--chunk N | --delta]   run the reviewer(s) (slow, shared);\n"
        "                  a gate gets [review].max_rounds (default 2) full rounds, then --delta/triage\n"
        "  ddflow review triage <id> --gate G --finding N --refuted|--confirmed --probe ...\n"
        "                                            record what became of one finding\n"
        "--finding/--refuted/--confirmed/--probe belong to the second form only. It REQUIRES the\n"
        "`triage` verb: without it they are refused, never run as a review.",
    )
    # `*`, not `?`: `ddflow review triage <id> ...` is a verb followed by the item.
    rw.add_argument("id", nargs="*", default=[], help="the item; or `triage <item>`")
    rw.add_argument("--gate", default=None, help="gate to review (default critic)")
    rw.add_argument(
        "--intent",
        default="",
        help="what the change is meant to do (defaults to the item's title/body)",
    )
    rw.add_argument("--context", default="")
    rw.add_argument("--base", default="")
    rw.add_argument(
        "--commit",
        default="",
        help="review this one landed commit (vs its first parent) instead of the item's branch",
    )
    rw.add_argument(
        "--branch",
        default="",
        help="review this branch against base -- for an item claimed without a worktree "
        "(default: the branch checked out in the worktree you are standing in)",
    )
    rw.add_argument(
        "--chunk",
        action="append",
        default=[],
        help="re-review only chunk N (as the recorded review numbered it; repeatable, or "
        "'2,5') and merge it into that record -- same diff, chunk size and reviewer",
    )
    rw.add_argument(
        "--delta",
        action="store_true",
        help="recheck ONLY what changed since the head the gate's last review covered: "
        "not a full round, never refused by [review].max_rounds (this is the default once "
        "the gate has a recorded review; [review].delta_default = false turns that off)",
    )
    rw.add_argument(
        "--full",
        action="store_true",
        help="review the item's WHOLE diff even though the gate has a recorded review: a "
        "full round, counted against [review].max_rounds",
    )
    rw.add_argument(
        "--force",
        action="store_true",
        help="run a full round past [review].max_rounds; needs --reason, recorded in the evidence",
    )
    rw.add_argument("--reason", default="", help="why --force")
    rw.add_argument("--finding", type=int, default=None, help="triage: the finding's number (#N)")
    rw.add_argument(
        "--refuted", action="store_true", help="triage: --probe shows the finding is false"
    )
    rw.add_argument(
        "--confirmed", action="store_true", help="triage: --probe is the fix/test that answers it"
    )
    rw.add_argument("--probe", default=None, help="triage: the evidence for the verdict")
    rw.set_defaults(fn=cmd_review)

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

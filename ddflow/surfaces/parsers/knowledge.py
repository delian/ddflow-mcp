"""`ddflow` subcommands: lessons, recall, similar, dupes, links and decisions.

Registered by `cli.build_parser`, in the order `ddflow --help` lists them."""

from __future__ import annotations

import argparse

from ...core.model import LINK_RELATIONS
from .. import dedupe_flags
from ..commands.decisions import cmd_decision
from ..commands.knowledge import cmd_dupes, cmd_lesson, cmd_link, cmd_recall, cmd_similar


def register(s: argparse._SubParsersAction) -> None:
    """Add the knowledge subcommands to `s`, the root `ddflow` subparsers."""
    ls = s.add_parser("lesson")
    ls_s = ls.add_subparsers(dest="lesson_cmd", required=True)
    la = ls_s.add_parser("add")
    la.add_argument("--id", default="")
    la.add_argument("--title", required=True)
    la.add_argument("--rule", default="")
    la.add_argument("--why", default="")
    la.add_argument("--how", default="")
    la.add_argument(
        "--summary",
        default="",
        help="the lesson in one paragraph; what docs/ddflow/LESSONS-SUMMARY.md is made of",
    )
    la.add_argument("--tags", default="")
    la.add_argument("--seen-in", default="")
    la.add_argument("--supersedes", default="")
    la.add_argument(
        "--pattern",
        default="",
        help="regex this lesson forbids. Scans NOW and stores WHICH sites match, so "
        "`lesson verify` can name what reappeared — a count could only say it got worse",
    )
    la.add_argument(
        "--globs", default="", help="comma-separated globs to scan (default: all tracked files)"
    )
    dedupe_flags.add_flags(la)
    la.set_defaults(fn=cmd_lesson)
    lv = ls_s.add_parser(
        "verify",
        help="re-scan every lesson's pattern and name the sites that reappeared",
    )
    lv.set_defaults(fn=cmd_lesson)
    lse = ls_s.add_parser("search")
    lse.add_argument("query")
    lse.add_argument("--limit", type=int, default=None, help="default: [lessons].max_results")
    lse.set_defaults(fn=cmd_lesson)

    rc = s.add_parser(
        "recall",
        help="'have we been here before?' — search decisions, lessons, research, bugs, "
        "tasks and past prompts at once",
    )
    rc.add_argument("query")
    rc.add_argument("--limit", type=int, default=3, help="hits per source")
    rc.add_argument(
        "--sources",
        default="",
        help="comma-separated subset: decisions,lessons,memories,research,bugs,items,prompts",
    )
    rc.add_argument("--max-chars", type=int, default=4000)
    rc.set_defaults(fn=cmd_recall)

    sm = s.add_parser(
        "similar",
        help="'is this already filed?' -- the existing bugs, tasks, lessons and other "
        "records most like a text, before you add it (read-only; exit 2 when none)",
    )
    sm.add_argument("text", help="the title or summary of the record you are about to file")
    sm.add_argument(
        "--kind",
        default="",
        help="comma-separated subset of [dedupe].kinds: "
        "bug,task,phase,lesson,decision,research,memory (default: all of them)",
    )
    sm.set_defaults(fn=cmd_similar)

    dp = s.add_parser(
        "dupes",
        help="'is anything filed twice?' -- the near-duplicate PAIRS already in the log, "
        "skipping pairs already linked or dismissed (read-only; exit 2 when none)",
    )
    dp.add_argument(
        "--kind",
        default="",
        help="comma-separated subset of [dedupe].kinds: "
        "bug,task,phase,lesson,decision,research,memory (default: all of them)",
    )
    dp.add_argument(
        "--open-only",
        action="store_true",
        help="only pairs where both records are still live (the dedupe_sweep pass)",
    )
    dp.add_argument(
        "--floor",
        type=float,
        default=None,
        help="minimum score to report (default: [dedupe].show_floor)",
    )
    dp.add_argument("--limit", type=int, default=0, help="at most N pairs (0 = all)")
    dp.set_defaults(fn=cmd_dupes)

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

    dc = s.add_parser("decision", help="architectural decisions: record and consult")
    dc_s = dc.add_subparsers(dest="decision_cmd", required=False)
    dca = dc_s.add_parser("add")
    dca.add_argument("--id", default="")
    dca.add_argument("--title", required=True)
    dca.add_argument("--decision", required=True, help="what was DECIDED (not what was discussed)")
    dca.add_argument("--context", default="", help="the forces: why a decision was needed")
    dca.add_argument("--consequences", default="", help="what it costs, incl. what it makes harder")
    dca.add_argument("--alternatives", default="", help="what was rejected, and why")
    dca.add_argument(
        "--globs",
        default="",
        help="the code this governs; without it the decision can only be found by search",
    )
    dca.add_argument("--tags", default="")
    dca.add_argument(
        "--sources",
        default="",
        help="where this came from: an ADR path, a URL, a commit sha (comma-separated)",
    )
    dca.add_argument("--item", default="")
    dca.add_argument("--by", default="", help="operator | agent | a name")
    dca.add_argument("--status", default="accepted", choices=["proposed", "accepted", "superseded"])
    dca.add_argument("--supersedes", default="")
    dedupe_flags.add_flags(dca)
    dca.set_defaults(fn=cmd_decision)
    dcl = dc_s.add_parser("list")
    dcl.add_argument("--all", action="store_true")
    dcl.add_argument(
        "--since", default=None, help="only decisions recorded at or after this ISO date"
    )
    dcl.add_argument("--limit", type=int, default=None, help="the newest N (0 = all)")
    dcl.set_defaults(fn=cmd_decision)
    dcs = dc_s.add_parser("show")
    dcs.add_argument("id")
    dcs.set_defaults(fn=cmd_decision)
    dcf = dc_s.add_parser("search")
    dcf.add_argument("query")
    dcf.add_argument("--limit", type=int, default=5)
    dcf.set_defaults(fn=cmd_decision)
    dcap = dc_s.add_parser("applicable", help="decisions governing an item's declared files")
    dcap.add_argument("id")
    dcap.set_defaults(fn=cmd_decision)
    dcsu = dc_s.add_parser("supersede")
    dcsu.add_argument("id")
    dcsu.add_argument("--by", required=True, help="the decision that replaces it")
    dcsu.add_argument("--reason", default="")
    dcsu.set_defaults(fn=cmd_decision)
    dc.set_defaults(fn=cmd_decision, decision_cmd="list", all=False)

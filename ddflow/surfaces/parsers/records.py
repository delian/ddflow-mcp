"""`ddflow` subcommands: status, research, bugs and sessions.

Registered by `cli.build_parser`, in the order `ddflow --help` lists them."""

from __future__ import annotations

import argparse

from .. import dedupe_flags
from ..commands.bug_reopen import add_bug_reopen_parser
from ..commands.knowledge import cmd_bug, cmd_research, cmd_session
from ..commands.reporting import cmd_status
from ..commands.viewers_sessions import add_session_view_parsers


def register(s: argparse._SubParsersAction) -> None:
    """Add the records subcommands to `s`, the root `ddflow` subparsers."""
    stt = s.add_parser("status", help="one answer to 'what is the state of this project?'")
    stt.set_defaults(fn=cmd_status)

    rs = s.add_parser("research")
    # `ddflow research add ...` as well as `ddflow research ...`: `lesson add`, `decision
    # add` and `memory add` all take the verb, the MCP tool is `ddflow_research_add`, and
    # the research gate's instruction says `research add` -- which this parser rejected
    # as "unrecognized arguments: add" for every agent that followed it.
    rs.add_argument(
        "verb", nargs="?", choices=["add"], help="optional: `research add` = `research`"
    )
    rs.add_argument("--id", default="")
    rs.add_argument("--question", required=True)
    rs.add_argument("--claim", default="")
    rs.add_argument("--mechanism", default="")
    rs.add_argument("--falsifier", default="")
    rs.add_argument("--probe", default="")
    rs.add_argument("--probe-output", default="")
    rs.add_argument("--verdict", required=True)
    rs.add_argument("--sources", default="")
    rs.add_argument("--budget", default="")
    rs.add_argument("--item", default="")
    dedupe_flags.add_flags(rs)
    rs.set_defaults(fn=cmd_research)

    bg = s.add_parser("bug")
    bg_s = bg.add_subparsers(dest="bug_cmd", required=True)
    bf = bg_s.add_parser("found")
    bf.add_argument("--id", default="")
    bf.add_argument("--summary", required=True)
    bf.add_argument("--item", default="")
    bf.add_argument("--title", default="", help="a short headline for the bug")
    bf.add_argument("--severity", default="", help="low | medium | high | critical (optional)")
    bf.add_argument(
        "--scope",
        default="",
        help="project (default), or ddflow for a bug in ddflow itself",
    )
    bf.add_argument(
        "--globs",
        default="",
        help="the fix task's files (comma-separated); default: the item's own globs",
    )
    bf.add_argument(
        "--no-task",
        action="store_true",
        help="file no fix task: the bug is fixed in the commit that found it",
    )
    dedupe_flags.add_flags(bf)
    bf.set_defaults(fn=cmd_bug)
    bft = bg_s.add_parser(
        "file-tasks",
        help="file a fix task for every open bug that has none (one-shot, after an upgrade)",
    )
    bft.add_argument("--dry-run", action="store_true", help="list what would be filed")
    bft.set_defaults(fn=cmd_bug)
    bx = bg_s.add_parser("fixed")
    bx.add_argument("id")
    bx.add_argument(
        "--regression-test",
        action="append",
        default=[],
        help="the test that now guards this bug; repeat it, or separate with ',' or ';', "
        "for several",
    )
    bx.add_argument(
        "--skip-regression-verify",
        dest="skip_verify",
        action="store_true",
        help="do not run the named test on the pre-fix tree; requires --verify-reason (recorded)",
    )
    bx.add_argument(
        "--verify-reason",
        default="",
        help="why the pre-fix regression check is skipped (recorded)",
    )
    bx.add_argument("--lesson", default="")
    bx.add_argument("--lesson-title", default="")
    bx.add_argument("--lesson-rule", default="")
    bx.add_argument(
        "--changelog", default="", help="'Fixed: text' (any category), or skip / internal"
    )
    bx.set_defaults(fn=cmd_bug)
    bv = bg_s.add_parser(
        "invalid", help="close a bug as a FALSE finding (never as fixed), with why and the probe"
    )
    bv.add_argument("id")
    bv.add_argument("--reason", required=True, help="why the finding is false")
    bv.add_argument(
        "--evidence", default="", help="the probe command or test node id that showed it"
    )
    bv.set_defaults(fn=cmd_bug)
    add_bug_reopen_parser(bg_s)

    se = s.add_parser("session")
    se_s = se.add_subparsers(dest="session_cmd", required=True)
    ss = se_s.add_parser("start")
    ss.add_argument("--model", default="")
    ss.add_argument("--tool", default="")
    ss.set_defaults(fn=cmd_session)
    sp = se_s.add_parser("prompt")
    sp.add_argument(
        "session",
        nargs="?",
        default="",
        help="the SESSION id (from `session start`), not the text; omitted: the latest "
        "open session, else an implicit new one",
    )
    sp.add_argument("--text", help="the prompt text; without it, read from piped stdin")
    sp.add_argument("--item", default="")
    sp.set_defaults(fn=cmd_session)
    sn = se_s.add_parser("note")
    sn.add_argument(
        "session",
        nargs="?",
        default="",
        help="the SESSION id (from `session start`), not the text; omitted: the latest "
        "open session, else an implicit new one",
    )
    sn.add_argument("--text", help="the note text; without it, read from piped stdin")
    sn.add_argument("--item", default="")
    sn.set_defaults(fn=cmd_session)
    so = se_s.add_parser(
        "adopt-orphans", help="attach prompts/notes recorded with no session id to a session"
    )
    so.set_defaults(fn=cmd_session)
    sd = se_s.add_parser("end")
    sd.add_argument("session")
    sd.add_argument("--summary", default="")
    sd.set_defaults(fn=cmd_session)
    add_session_view_parsers(se_s)

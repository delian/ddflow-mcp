"""`ddflow` subcommands: brief, external sync, long-running jobs and memory.

Registered by `cli.build_parser`, in the order `ddflow --help` lists them."""

from __future__ import annotations

import argparse

from ...api import lifecycle as A_LIFECYCLE
from .. import dedupe_flags
from ..commands.knowledge import cmd_job, cmd_memory
from ..commands.lifecycle import cmd_brief
from ..commands.operations import cmd_external


def register(s: argparse._SubParsersAction) -> None:
    """Add the jobs subcommands to `s`, the root `ddflow` subparsers."""
    br = s.add_parser("brief", help="budgeted session-start pack")
    br.add_argument("--item", default="")
    br.add_argument("--phase", default="")
    br.add_argument(
        "--check-recovery", action="store_true", default=A_LIFECYCLE.DEFAULT_CHECK_RECOVERY
    )
    br.set_defaults(fn=cmd_brief)

    ex = s.add_parser("external", help="dependencies on items in sibling repositories")
    ex_s = ex.add_subparsers(dest="external_cmd", required=True)
    ex_s.add_parser(
        "sync", help="observe the sibling-repo items `needs` names; record what changed"
    ).set_defaults(fn=cmd_external)

    jb = s.add_parser("job", help="long-running processes an item waits on: run, add, list, end")
    jb_s = jb.add_subparsers(dest="job_cmd", required=True)
    jr = jb_s.add_parser("run", help="launch a command detached for an item and record it")
    jr.add_argument("item")
    jr.add_argument("command", help="shell command (quote it)")
    jr.add_argument("--log", default="", help="output file (default .ddflow/local/jobs/)")
    jr.add_argument("--cwd", default="", help="default: the item's worktree, else the repo")
    jr.set_defaults(fn=cmd_job)
    ja = jb_s.add_parser("add", help="register a process started some other way")
    ja.add_argument("item")
    ja.add_argument("--pid", type=int, required=True)
    ja.add_argument("--command", default="")
    ja.add_argument("--log", default="")
    ja.set_defaults(fn=cmd_job)
    jl = jb_s.add_parser("list", help="jobs with their live status (exit 2 = none)")
    jl.add_argument("--item", default="")
    jl.add_argument("--all", action="store_true", help="include ended jobs")
    jl.set_defaults(fn=cmd_job)
    je = jb_s.add_parser("end", help="record how a job ended (refused while it runs)")
    je.add_argument("job")
    je.add_argument("--exit-code", type=int, default=None, help="default: the one its log recorded")
    je.add_argument("--note", default="")
    je.add_argument(
        "--force", action="store_true", help="end a job on another host you have checked there"
    )
    je.set_defaults(fn=cmd_job)

    me = s.add_parser(
        "memory", help="operational facts about this machine/repo, shown at session start"
    )
    me_s = me.add_subparsers(dest="memory_cmd", required=True)
    ma = me_s.add_parser("add", help="remember one fact (refused over [memory] max_chars)")
    ma.add_argument("text")
    ma.add_argument("--tags", default="")
    ma.add_argument("--id", default="", help="re-record (correct) an existing memory")
    dedupe_flags.add_flags(ma)
    ma.set_defaults(fn=cmd_memory)
    ml = me_s.add_parser("list", help="live memories, newest first (exit 2 = none)")
    ml.add_argument("--query", default="", help="rank by relevance instead of recency")
    ml.add_argument("--limit", type=int, default=0)
    ml.add_argument("--all", action="store_true", help="include forgotten memories")
    ml.set_defaults(fn=cmd_memory)
    mf = me_s.add_parser("forget", help="stop believing a memory; kept, with the reason")
    mf.add_argument("id")
    mf.add_argument("--reason", required=True)
    mf.set_defaults(fn=cmd_memory)

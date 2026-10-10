"""The reporting commands, declared once: replay, recover, progress, loops, cleanup, rebuild,
render, board, show, status and history, with the tools they have.

`surfaces/parsers/` registers their command-line halves and `surfaces/tools/` takes their MCP
entries (D-unify 4, B-uni-cmd-migrate.6d-reporting). `doctor` is declared with the setup
commands (`declared/setup.py`); `identify` has no command line and stays a hand-written tool.
"""

from __future__ import annotations

from ...core.defaults import DEFAULT_RENDER_DIR
from ..argtypes import _positive_int
from ..registry import Command, Param, by_tool
from ..tools._common import _api

COMMANDS: tuple[Command, ...] = (
    Command(
        path=("replay",),
        tool="ddflow_replay",
        summary="reconstruct the decision history from the log",
        description=(
            "Reconstruct the project's whole decision history from the log: every "
            "operator prompt in order, every architectural decision, every research "
            "verdict, every lesson, and the shape of the queue. This is what rebuilds "
            "the project if the code is lost — it reproduces the DECISIONS, not the "
            "bytes."
        ),
        params=(
            Param("out", help="Write a recovery kit to this directory.", default="", cli_help=""),
            Param(
                "verify",
                type="boolean",
                help="Re-resolve every recorded commit sha against this repository and report the ones that are gone: a reconstruction citing unresolvable shas is a narrative, not a record.",
                cli_help="",
            ),
        ),
        call=lambda repo, a, agent: _api().replay(
            repo, out_dir=a.get("out", "") or "", verify=bool(a.get("verify")), agent=agent
        ),
        payload="text",
        prose=True,
        prose_reason="the reconstruction narrative; the whole output is the deliverable",
        kind="replay",
    ),
    Command(
        path=("recover",),
        tool="ddflow_recover",
        summary="find crashed agents' work (exit 2 = nothing)",
        description=(
            "Find work left behind by a crashed agent: expired leases, orphaned "
            "worktrees, items stuck running. Reports what each worktree contains and "
            "never deletes anything. Run this at the start of any session that follows "
            "an interruption."
        ),
        params=(
            Param("item", help="Restrict to one item.", default="", cli_help=""),
            Param(
                "apply",
                type="boolean",
                help="Act on the advice: release the leases and remove the worktrees reported as holding nothing. Only a tree MEASURED as having no uncommitted and no unmerged work is touched; 'could not tell' is not 'empty'.",
                cli_help="",
            ),
        ),
        call=lambda repo, a, agent: _api().recover(
            repo, item=a.get("item", "") or "", apply=bool(a.get("apply")), agent=agent
        ),
        payload="found",
    ),
    Command(
        path=("progress",),
        tool="ddflow_progress",
        summary="work actually done, aggregated from the log",
        description=(
            "What work has ACTUALLY been done, aggregated from the event log: attempts "
            "per item, wall-clock held, gate runs, commits produced, and who did them. "
            "Use it to answer 'how much effort has gone into this' and to see an item's "
            "full gate history including the outcomes that were not passes."
        ),
        params=(
            Param(
                "id",
                help="One item, with its per-attempt detail.",
                positional=True,
                nargs="?",
                default="",
                cli_help="",
            ),
            Param(
                "limit",
                type="integer",
                help="Rows returned, most effort first (default 25; 0 = all).",
                mcp_only=True,
            ),
        ),
        call=lambda repo, a, agent: _api().progress(repo, a.get("id", "") or ""),
        # The pre-migration body was the ROW ARRAY. Preserved exactly.
        payload="rows",
    ),
    Command(
        path=("loops",),
        tool="ddflow_loops",
        summary="circular references and runtime loops (exit 2 = none)",
        description=(
            "Detect circular references and runtime loops: dependency cycles, an item "
            "claimed and given up over and over, a gate whose verdict keeps flipping, "
            "a gate failing again with identical output (repeated_failure), "
            "work completed and reopened repeatedly, duplicate items writing the same "
            "files, and a queue where events keep arriving but nothing advances. "
            "CALL THIS WHEN WORK FEELS REPETITIVE: stop and re-plan rather than retry "
            "the same thing. [] when nothing is wrong."
        ),
        params=(),
        call=lambda repo, a, agent: _api().loops(repo),
        # The pre-migration body was the findings ARRAY. Preserved exactly.
        payload="findings",
    ),
    Command(
        path=("cleanup",),
        tool="ddflow_cleanup",
        summary="classify ddflow worktrees/branches; --apply lands the safe ones",
        description=(
            "Classify every ddflow worktree and branch: merged (safe to remove), unmerged (carries commits nobody landed), dirty (uncommitted edits: a human looks), orphan, or stale branch. Reports by default; apply=true removes merged worktrees and branches and lands commits for items the queue considers done. A dirty tree is NEVER touched automatically: it exists nowhere else."
        ),
        params=(Param("apply", type="boolean", help="Perform the safe actions.", cli_help=""),),
        call=lambda repo, a, agent: _api().cleanup(repo, apply=bool(a.get("apply")), agent=agent),
        payload=("trees", "stale_branches"),
    ),
    Command(
        path=("rebuild",),
        tool="ddflow_rebuild",
        summary="re-derive the index from the log",
        description=(
            "Re-derive the search index from the event log. The index is a "
            "disposable cache; this is never a data-loss operation."
        ),
        params=(),
        call=lambda repo, a, agent: _api().rebuild(repo, agent=agent),
        payload=("events", "items"),
    ),
    Command(
        path=("render",),
        tool="ddflow_render",
        summary="regenerate the human-readable views",
        description=(
            "Regenerate the human-readable markdown views (queue, lessons, the "
            "one-paragraph lessons summary, research) under docs/ddflow/."
        ),
        params=(
            Param(
                "out",
                help="Directory for the generated views (default: docs/ddflow).",
                default=DEFAULT_RENDER_DIR,
                cli_help="",
            ),
            Param(
                "show",
                help="Print ONE view instead of writing files: lessons, lessons-summary, research, or board. This is what the ddflow:// resources are served from.",
                default="",
                cli_help="print ONE view to stdout instead of writing files: lessons, lessons-summary, research, board",
            ),
        ),
        call=lambda repo, a, agent: _api().render(
            repo,
            show=a.get("show", "") or "",
            out_dir=a.get("out") or _api().DEFAULT_RENDER_DIR,
            agent=agent,
        ),
        # Two shapes, both pre-existing: `--show` returned the DOCUMENT and without it
        # the answer was the list of files written. A predicate, because which one it
        # is cannot be known until the call.
        payload=lambda a: "text" if a.get("show") else ("files",),
        prose=lambda a: bool(a.get("show")),
        prose_reason="with --show it returns the rendered view itself, to read or commit",
        kind="render",
    ),
    Command(
        path=("board",),
        tool="ddflow_board",
        description="The whole work queue as a readable board, with the critical path.",
        params=(Param("phase", help="Restrict to one phase.", default="", cli_help=""),),
        call=lambda repo, a, agent: _api().board(repo, phase=a.get("phase", "") or "", agent=agent),
        payload="text",
        prose=True,
        prose_reason="a rendered markdown board, meant to be shown or committed as-is",
        kind="board",
    ),
    Command(
        path=("show",),
        tool="ddflow_show",
        description=(
            "Everything known about one phase, task or bug (a bug id works too): state, dependencies, declared "
            "globs, the lease and who holds it, the worktree path you can cd to, and "
            "every gate's outcome with its evidence. Use it to check your own work "
            "before calling ddflow_complete."
        ),
        params=(
            Param(
                "id",
                help="Item id (phase, task) or bug id.",
                positional=True,
                required=True,
                cli_help="",
            ),
        ),
        call=lambda repo, a, agent: _api().show(repo, a["id"], agent=agent),
        payload="item",
    ),
    Command(
        path=("status",),
        tool="ddflow_status",
        summary="one answer to 'what is the state of this project?'",
        description=(
            "The state of the whole project in one answer: how many tasks are done and "
            "which, what is in flight and who holds it, what is ready to start, what is "
            "blocked, how many agent-hours and commits went in, and whether anything is "
            "looping or waiting to be recovered. This is the tool for 'what is the "
            "status of this project?' and 'what has been completed?'."
        ),
        params=(),
        call=lambda repo, a, agent: _api().status(repo, agent=agent),
        # The whole object. `_render` is stripped by `Outcome.body`.
        payload="",
    ),
    Command(
        path=("history",),
        tool="ddflow_history",
        summary="one timeline of everything that happened (exit 2 = nothing)",
        description=(
            "ONE timeline of everything that happened: claims, releases, gates, bugs, "
            "decisions, lessons, completions. Other views say what is true now; this says "
            "how it got that way.\n\n"
            "Filter with `item` (one task's life), `kind` (a family: 'gate', "
            "'lease.acquired', 'decision,bug'), `since`, `by_agent`. Exit 2 means nothing "
            "matched: an answer, not a failure."
        ),
        params=(
            Param(
                "item",
                help="Restrict to one item's timeline.",
                default="",
                cli_help="restrict to one item",
            ),
            Param(
                "kind",
                help="Comma-separated event kinds or families: 'gate', 'lease.acquired', 'decision,bug'.",
                default="",
                cli_help="comma-separated event kinds or families: 'gate', 'lease.acquired', 'decision,bug'",
            ),
            Param(
                "since",
                help="ISO timestamp lower bound.",
                default="",
                cli_help="ISO timestamp lower bound",
            ),
            Param(
                "limit",
                type="integer",
                help="Most recent N entries (default 40).",
                default=40,
                cli_help="",
            ),
            Param(
                "by_agent",
                flag="--agent",
                metavar="LOG_AGENT",
                help="Only this agent's events.",
                default="",
                cli_help="only this agent's shard",
            ),
            Param(
                "tail",
                type="integer",
                help="Last N entries, oldest first (overrides limit).",
                default=0,
                cli_type=_positive_int,
                cli_help="the last N events, oldest first",
            ),
        ),
        tool_order=("item", "kind", "since", "limit", "tail", "by_agent"),
        call=lambda repo, a, agent: _api().history(
            repo,
            item=a.get("item", "") or "",
            kind=a.get("kind", "") or "",
            since=a.get("since", "") or "",
            limit=int(a.get("limit") or 40),
            agent=agent,
            by_agent=a.get("by_agent", "") or "",
            tail=int(a.get("tail") or 0),
        ),
        payload=("total", "shown", "events"),
    ),
)

BY_TOOL = by_tool(COMMANDS)

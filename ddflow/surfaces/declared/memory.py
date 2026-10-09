"""The job and memory commands, declared once: ``job run|add|list|end`` and
``memory add|list|forget``, with the tools they have.

`surfaces/parsers/jobs.py` registers their command-line halves and `surfaces/tools/jobs.py`
takes their MCP entries (D-unify 4, B-uni-cmd-migrate.6g-memory). Their handlers stay in
`surfaces/commands/knowledge.py`; the parser supplies them by path.
"""

from __future__ import annotations

from ..registry import Command, Param, by_tool
from ..tools._common import _answer, _api
from .answer import ANSWER_FLAG_EXEMPT, ANSWER_PARAMS

#: The one-line help of each group, as ``ddflow --help`` lists it.
GROUPS: dict[str, str] = {
    "job": "long-running processes an item waits on: run, add, list, end",
    "memory": "operational facts about this machine/repo, shown at session start",
}

COMMANDS: tuple[Command, ...] = (
    Command(
        path=("job", "run"),
        summary="launch a command detached for an item and record it",
        tool="ddflow_job_run",
        description=(
            "Launch a LONG-RUNNING command for an item (a training run, a data generation, a model server) detached into its own session, so it outlives you, this server and a restarted remote-control service, and record it. Runs in the item's worktree; returns the job id, pid and log path. Then WAIT with ddflow_job_list rather than polling; `ddflow_brief` shows running jobs to the next session. Declare the item's `resources` (ddflow_update) so nobody else starts a run on the same GPUs."
        ),
        call=lambda repo, a, agent: _api().job_run(
            repo,
            a["item"],
            a["command"],
            log_file=a.get("log", "") or "",
            cwd=a.get("cwd", "") or "",
            agent=agent,
        ),
        payload=("id", "pid", "log", "cwd"),
        params=(
            Param("item", help="Item the job is for.", cli_help="", positional=True),
            Param(
                "command",
                help="The shell command.",
                cli_help="shell command (quote it)",
                positional=True,
            ),
            Param(
                "log",
                help="Output file (default .ddflow/local/jobs/<item>-<t>.log).",
                cli_help="output file (default .ddflow/local/jobs/)",
                default="",
            ),
            Param(
                "cwd",
                help="Working directory (default: the item's worktree).",
                cli_help="default: the item's worktree, else the repo",
                default="",
            ),
        ),
    ),
    Command(
        path=("job", "add"),
        summary="register a process started some other way",
        tool="ddflow_job_add",
        description=(
            "Register a long-running process you started some other way (torchrun, a "
            "launcher script), by pid, while it runs -- so its liveness can be checked by "
            "anyone later, including after a pid is reused."
        ),
        call=lambda repo, a, agent: _api().job_add(
            repo,
            a["item"],
            int(a["pid"]),
            command=a.get("command", "") or "",
            log_file=a.get("log", "") or "",
            agent=agent,
        ),
        payload=("id", "pid", "log", "cwd"),
        params=(
            Param("item", help="Item the job is for.", cli_help="", positional=True),
            Param("pid", type="integer", help="Its process id.", cli_help="", required=True),
            Param("command", help="What it is running, for humans.", cli_help="", default=""),
            Param("log", help="Where its output goes.", cli_help="", default=""),
        ),
    ),
    Command(
        path=("job", "list"),
        summary="jobs with their live status (exit 2 = none)",
        tool="ddflow_job_list",
        description=(
            "Long-running jobs and their LIVE status: running, exited (with the exit code "
            "its log recorded), gone (killed: no exit recorded), elsewhere (another host), "
            "or ended. Use it to decide whether to keep waiting, collect results, or "
            "restart. A long run is a WAIT, never a reason to stop working the queue."
        ),
        call=lambda repo, a, agent: _api().job_list(
            repo, item=a.get("item", "") or "", include_ended=bool(a.get("all")), agent=agent
        ),
        payload="jobs",
        params=(
            Param("item", help="Only this item's jobs.", cli_help="", default=""),
            Param(
                "all",
                type="boolean",
                help="Include jobs already recorded as ended.",
                cli_help="include ended jobs",
            ),
        ),
    ),
    Command(
        path=("job", "end"),
        summary="record how a job ended (refused while it runs)",
        tool="ddflow_job_end",
        description=(
            "Record that a job ended and how. Refused while the process is still running. "
            "The exit code defaults to the one its log recorded."
        ),
        call=lambda repo, a, agent: _api().job_end(
            repo,
            a["job"],
            exit_code=a.get("exit_code"),
            note=a.get("note", "") or "",
            force=bool(a.get("force")),
            agent=agent,
        ),
        payload=("id", "exit_code"),
        params=(
            Param("job", help="Job id.", cli_help="", positional=True),
            Param(
                "exit_code",
                type="integer",
                help="Override the recorded exit code.",
                cli_help="default: the one its log recorded",
                default=None,
            ),
            Param(
                "note",
                help="What came of it: metrics, where the output is.",
                cli_help="",
                default="",
            ),
            Param(
                "force",
                type="boolean",
                help="End a job that runs on ANOTHER host, after checking it there. Without it such a job is refused: 'could not look' is not 'not running'.",
                cli_help="end a job on another host you have checked there",
            ),
        ),
    ),
    Command(
        path=("memory", "add"),
        summary="remember one fact (refused over [memory] max_chars)",
        tool="ddflow_memory_add",
        flag_exempt=ANSWER_FLAG_EXEMPT,
        description=(
            "Remember ONE operational fact about this machine, repository or working state ('this box has 8 H200s', 'use -n 16, never -n auto'). Shown at the top of every ddflow_brief and found by ddflow_recall, in every worktree at once. Not for rules (ddflow_lesson_add), events (ddflow_session_note) or how the software is built (ddflow_decision_add). Never a secret: the log is committed. Refused over [memory] max_chars (default 280)."
        ),
        call=lambda repo, a, agent: _api().memory_add(
            repo,
            a.get("text", "") or "",
            tags=a.get("tags", "") or "",
            id=a.get("id", "") or "",
            answer=_answer(a),
            agent=agent,
        ),
        payload=("id", "replaced"),
        params=(
            Param("text", help="The fact, in one or two sentences.", cli_help="", positional=True),
            Param(
                "tags",
                help="Comma-separated tags, e.g. 'gpu,machine'.",
                cli_help="",
                default="",
            ),
            Param(
                "id",
                help="Re-record an existing memory under its id -- how a fact is CORRECTED. Omit for a new one.",
                cli_help="re-record (correct) an existing memory",
                default="",
            ),
            *ANSWER_PARAMS,
        ),
    ),
    Command(
        path=("memory", "list"),
        summary="live memories, newest first (exit 2 = none)",
        tool="ddflow_memory_list",
        description=(
            "The project's operational memories, newest first -- or ranked against "
            "`query`. What an agent must know before touching anything on this machine; "
            "read them at session start if ddflow_brief truncated the list."
        ),
        call=lambda repo, a, agent: _api().memory_list(
            repo,
            query=a.get("query", "") or "",
            limit=int(a.get("limit") or 0),
            include_forgotten=bool(a.get("all")),
            agent=agent,
        ),
        payload=("memories", "total_live"),
        params=(
            Param(
                "query",
                help="Rank by relevance to this instead of by recency.",
                cli_help="rank by relevance instead of recency",
                default="",
            ),
            Param(
                "limit",
                type="integer",
                help="At most this many (default: all).",
                cli_help="",
                default=0,
            ),
            Param(
                "all",
                type="boolean",
                help="Include forgotten memories, with why they were forgotten.",
                cli_help="include forgotten memories",
            ),
        ),
    ),
    Command(
        path=("memory", "forget"),
        summary="stop believing a memory; kept, with the reason",
        tool="ddflow_memory_forget",
        description=(
            "Stop believing a memory that is no longer true. It is kept, with the reason: "
            "'we thought X until Y' is what stops the next agent re-learning X. Correct a "
            "fact instead with ddflow_memory_add and its id."
        ),
        call=lambda repo, a, agent: _api().memory_forget(
            repo, a["id"], reason=a.get("reason", "") or "", agent=agent
        ),
        payload=("id",),
        params=(
            Param("id", help="Memory id.", cli_help="", positional=True),
            Param("reason", help="Why it is no longer true.", cli_help="", required=True),
        ),
    ),
)

BY_TOOL = by_tool(COMMANDS)

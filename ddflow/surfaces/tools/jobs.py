"""MCP tools: long-running jobs, memory, resolve/unblock, session start.

One slice of the `TOOLS` registry, assembled in `surfaces/tools/__init__.py`."""

from __future__ import annotations

from typing import Any

from ..declared import records as R
from ._common import _answer, _api

TOOLS: dict[str, dict[str, Any]] = {
    "ddflow_job_run": {
        "description": (
            "Launch a LONG-RUNNING command for an item (a training run, a data generation, a model server) detached into its own session, so it outlives you, this server and a restarted remote-control service, and record it. Runs in the item's worktree; returns the job id, pid and log path. Then WAIT with ddflow_job_list rather than polling; `ddflow_brief` shows running jobs to the next session. Declare the item's `resources` (ddflow_update) so nobody else starts a run on the same GPUs."
        ),
        "properties": {
            "item": ("string", "Item the job is for.", True),
            "command": ("string", "The shell command.", True),
            "log": ("string", "Output file (default .ddflow/local/jobs/<item>-<t>.log).", False),
            "cwd": ("string", "Working directory (default: the item's worktree).", False),
        },
        "api": lambda repo, a, agent: _api().job_run(
            repo,
            a["item"],
            a["command"],
            log_file=a.get("log", "") or "",
            cwd=a.get("cwd", "") or "",
            agent=agent,
        ),
        "payload": ("id", "pid", "log", "cwd"),
    },
    "ddflow_job_add": {
        "description": (
            "Register a long-running process you started some other way (torchrun, a "
            "launcher script), by pid, while it runs -- so its liveness can be checked by "
            "anyone later, including after a pid is reused."
        ),
        "properties": {
            "item": ("string", "Item the job is for.", True),
            "pid": ("integer", "Its process id.", True),
            "command": ("string", "What it is running, for humans.", False),
            "log": ("string", "Where its output goes.", False),
        },
        "api": lambda repo, a, agent: _api().job_add(
            repo,
            a["item"],
            int(a["pid"]),
            command=a.get("command", "") or "",
            log_file=a.get("log", "") or "",
            agent=agent,
        ),
        "payload": ("id", "pid", "log", "cwd"),
    },
    "ddflow_job_list": {
        "description": (
            "Long-running jobs and their LIVE status: running, exited (with the exit code "
            "its log recorded), gone (killed: no exit recorded), elsewhere (another host), "
            "or ended. Use it to decide whether to keep waiting, collect results, or "
            "restart. A long run is a WAIT, never a reason to stop working the queue."
        ),
        "properties": {
            "item": ("string", "Only this item's jobs.", False),
            "all": ("boolean", "Include jobs already recorded as ended.", False),
        },
        "api": lambda repo, a, agent: _api().job_list(
            repo, item=a.get("item", "") or "", include_ended=bool(a.get("all")), agent=agent
        ),
        "payload": "jobs",
    },
    "ddflow_job_end": {
        "description": (
            "Record that a job ended and how. Refused while the process is still running. "
            "The exit code defaults to the one its log recorded."
        ),
        "properties": {
            "job": ("string", "Job id.", True),
            "exit_code": ("integer", "Override the recorded exit code.", False),
            "note": ("string", "What came of it: metrics, where the output is.", False),
            "force": (
                "boolean",
                "End a job that runs on ANOTHER host, after checking it there. Without it "
                "such a job is refused: 'could not look' is not 'not running'.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().job_end(
            repo,
            a["job"],
            exit_code=a.get("exit_code"),
            note=a.get("note", "") or "",
            force=bool(a.get("force")),
            agent=agent,
        ),
        "payload": ("id", "exit_code"),
    },
    "ddflow_memory_add": {
        "description": (
            "Remember ONE operational fact about this machine, repository or working state ('this box has 8 H200s', 'use -n 16, never -n auto'). Shown at the top of every ddflow_brief and found by ddflow_recall, in every worktree at once. Not for rules (ddflow_lesson_add), events (ddflow_session_note) or how the software is built (ddflow_decision_add). Never a secret: the log is committed. Refused over [memory] max_chars (default 280)."
        ),
        "properties": {
            "text": ("string", "The fact, in one or two sentences.", True),
            "tags": ("string", "Comma-separated tags, e.g. 'gpu,machine'.", False),
            "id": (
                "string",
                "Re-record an existing memory under its id -- how a fact is CORRECTED. "
                "Omit for a new one.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().memory_add(
            repo,
            a.get("text", "") or "",
            tags=a.get("tags", "") or "",
            id=a.get("id", "") or "",
            answer=_answer(a),
            agent=agent,
        ),
        "payload": ("id", "replaced"),
    },
    "ddflow_memory_list": {
        "description": (
            "The project's operational memories, newest first -- or ranked against "
            "`query`. What an agent must know before touching anything on this machine; "
            "read them at session start if ddflow_brief truncated the list."
        ),
        "properties": {
            "query": ("string", "Rank by relevance to this instead of by recency.", False),
            "limit": ("integer", "At most this many (default: all).", False),
            "all": ("boolean", "Include forgotten memories, with why they were forgotten.", False),
        },
        "api": lambda repo, a, agent: _api().memory_list(
            repo,
            query=a.get("query", "") or "",
            limit=int(a.get("limit") or 0),
            include_forgotten=bool(a.get("all")),
            agent=agent,
        ),
        "payload": ("memories", "total_live"),
    },
    "ddflow_memory_forget": {
        "description": (
            "Stop believing a memory that is no longer true. It is kept, with the reason: "
            "'we thought X until Y' is what stops the next agent re-learning X. Correct a "
            "fact instead with ddflow_memory_add and its id."
        ),
        "properties": {
            "id": ("string", "Memory id.", True),
            "reason": ("string", "Why it is no longer true.", True),
        },
        "api": lambda repo, a, agent: _api().memory_forget(
            repo, a["id"], reason=a.get("reason", "") or "", agent=agent
        ),
        "payload": ("id",),
    },
    "ddflow_resolve": {
        "description": (
            "Settle a CONTESTED item: two clones each added the same id with different content, or each claimed it, and a merge brought both in (`ddflow_doctor` names them, `ddflow_show` lists the rivals, `ddflow_next` withholds them). `keep` names the definition (event id or agent) and/or the lease holder to keep; the losing claim is released in the same transaction; a losing DEFINITION comes back in `lost`: re-add it under a new id with `refile_as`, or it stays only in the log. Refused (exit 3) when not contested."
        ),
        "properties": {
            "id": ("string", "The contested item.", True),
            "keep": (
                "string",
                "Event id (or a 6+ character prefix), agent, or lease holder to keep.",
                True,
            ),
            "refile_as": (
                "string",
                "Comma-separated new ids, one per definition NOT kept, in `show` order: "
                "each is re-added under its new id in the same transaction.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().resolve(
            repo, a["id"], keep=a["keep"], refile_as=a.get("refile_as", "") or "", agent=agent
        ),
        "payload": ("id", "kept_definition", "kept_holder", "lost", "refiled", "released"),
    },
    "ddflow_unblock": {
        "description": (
            "Release a BLOCKED item -- and every blocked item beneath it -- back into "
            "the queue, so `next` can offer them again. The inverse of ddflow_block, and "
            "how deferred work, or a whole archived section an import landed as blocked, "
            "becomes work once the OPERATOR says so: pass a phase id to release its "
            "section. Do not release held work on your own judgement. Returns "
            "nothing-to-do (exit 2) when nothing there is blocked."
        ),
        "properties": {
            "id": ("string", "Item id.", True),
            "note": ("string", "Why it is work again (who decided, and when).", False),
        },
        "api": lambda repo, a, agent: _api().unblock(
            repo, a["id"], note=a.get("note", "") or "", agent=agent
        ),
        "payload": ("id", "was", "released"),
    },
    "ddflow_session_start": R.BY_TOOL["ddflow_session_start"].tool_entry(),
}

"""MCP tools: recording as you go: bugs found, session notes, decision show.

One slice of the `TOOLS` registry, assembled in `surfaces/tools/__init__.py`."""

from __future__ import annotations

from typing import Any

from ..declared import knowledge as K
from ._common import _answer, _api

TOOLS: dict[str, dict[str, Any]] = {
    "ddflow_bug_found": {
        "description": (
            "Report a bug the moment you find it, BEFORE fixing it. Recording it first "
            "makes the fix accountable: `ddflow_bug_fixed` refuses to close one without "
            "a regression test. A hunt that records nothing looks like one that found nothing."
        ),
        "properties": {
            "id": ("string", "Stable id, e.g. 'B1'. You will cite it when closing.", False),
            "summary": ("string", "What is wrong, in one line.", True),
            "item": ("string", "The task it was found in or affects.", False),
            "title": ("string", "Short headline.", False),
            "severity": ("string", "low|medium|high|critical.", False),
            "scope": ("string", "project (default) or ddflow.", False),
            "globs": ("string", "The fix task's files; default: the item's globs.", False),
            "no_task": ("boolean", "File no fix task (fixed in the same commit).", False),
        },
        "api": lambda repo, a, agent: _api().bug_found(
            repo,
            summary=a.get("summary", "") or "",
            item=a.get("item", "") or "",
            id=a.get("id", "") or "",
            title=a.get("title", "") or "",
            severity=a.get("severity", "") or "",
            scope=a.get("scope", "") or "",
            globs=a.get("globs", "") or "",
            no_task=bool(a.get("no_task")),
            answer=_answer(a),
            agent=agent,
        ),
        "payload": ("id", "offer", "fix_task", "fix_task_filed"),
    },
    "ddflow_bug_file_tasks": {
        "description": (
            "File a fix task for every open bug that has none (one-shot after an upgrade; "
            "`ddflow_bug_found` files one per bug by default)."
        ),
        "properties": {
            "dry_run": ("boolean", "List what would be filed; write nothing.", False),
        },
        "api": lambda repo, a, agent: _api().bug_file_tasks(
            repo, dry_run=bool(a.get("dry_run")), agent=agent
        ),
        "payload": ("filed", "linked", "tasks", "links", "dry_run"),
    },
    "ddflow_session_note": {
        "description": (
            "Record something that happened during a session which is neither an "
            "operator prompt nor a decision — a surprise, a dead end, why you changed "
            "approach. It goes into the reconstruction alongside the prompts, and a "
            "dead end recorded is a dead end nobody walks down twice."
        ),
        "properties": {
            "session": (
                "string",
                "Session id; omit for the latest open.",
                False,
            ),
            "text": ("string", "The note.", True),
            "item": ("string", "Item it concerns.", False),
        },
        "api": lambda repo, a, agent: _api().session_note(
            repo,
            a.get("session", "") or "",
            a.get("text", "") or "",
            item=a.get("item", "") or "",
            agent=agent,
        ),
        "payload": ("session", "how"),
    },
    "ddflow_session_end": {
        "description": (
            "Close a session with a summary of what it achieved. The summary is what a "
            "later reader sees before deciding whether to open the whole transcript, "
            "so write it for someone who was not there."
        ),
        "properties": {
            "session": ("string", "Session id.", True),
            "summary": ("string", "What this session achieved.", False),
        },
        "api": lambda repo, a, agent: _api().session_end(
            repo, a.get("session", "") or "", summary=a.get("summary", "") or "", agent=agent
        ),
        "payload": ("session",),
    },
    "ddflow_decision_show": K.BY_TOOL["ddflow_decision_show"].tool_entry(),
}

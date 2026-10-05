"""MCP tools: adding work to the queue: phases, splits, tasks.

One slice of the `TOOLS` registry, assembled in `surfaces/tools/__init__.py`."""

from __future__ import annotations

from typing import Any

from ._common import _answer, _api

TOOLS: dict[str, dict[str, Any]] = {
    "ddflow_phase_add": {
        "description": (
            "Add a phase to the queue. A phase is a unit of REVIEW: it gets its own "
            "research, its own whole-phase test pass and live smoke run, and it merges "
            "as one coherent feature. Group tasks into a phase when they only make "
            "sense shipped together."
        ),
        "properties": {
            "id": ("string", "Short stable id, e.g. 'P2' or 'auth'.", True),
            "title": ("string", "One-line description.", False),
            "needs": ("string", "Comma-separated ids this phase depends on.", False),
            "globs": (
                "string",
                "Comma-separated path globs this phase writes: what lets two agents work different phases in parallel; the phase's dependencies are INHERITED by every task in it.",
                False,
            ),
            "body": ("string", "Detail, acceptance criteria, context.", False),
            "tags": ("string", "Comma-separated tags.", False),
            "priority": ("integer", "Lower is offered first (default 100).", False),
            "line": (
                "string",
                "Release line this lands on (a name from [flow.lines], or the current "
                "line). Omit for the current line; tasks inherit a phase's line.",
                False,
            ),
            "readd": (
                "boolean",
                "File a REMOVED item's id again with this definition. An id still in the "
                "queue is always refused -- change that item with ddflow_update.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().phase_add(
            repo,
            a["id"],
            title=a.get("title", "") or "",
            needs=a.get("needs", "") or "",
            globs=a.get("globs", "") or "",
            body=a.get("body", "") or "",
            tags=a.get("tags", "") or "",
            priority=int(a.get("priority") or _api().DEFAULT_PRIORITY),
            line=a.get("line", "") or "",
            readd=bool(a.get("readd")),
            answer=_answer(a),
            agent=agent,
        ),
        "payload": ("id",),
    },
    "ddflow_split": {
        "description": (
            "Split an item into sub-tasks IN PLACE when the work turns out to be two things -- the moment you discover it; mid-task discovery is normal. The original keeps its id and history and becomes an umbrella that completes when its children do; closing it and opening two new ones would lose the thread between what was planned and what happened. Children inherit the parent's globs: give each its own afterwards if they write different files, or they cannot run in parallel."
        ),
        "properties": {
            "id": ("string", "The item to split.", True),
            "into": ("string", "Comma-separated 'sub-id=title' pairs. At least two.", True),
            "globs": ("string", "Globs for the children (default: inherit).", False),
            "needs": (
                "string",
                "Dependencies for the FIRST child. The others chain from it if you set "
                "theirs with ddflow_update.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().split(
            repo,
            a["id"],
            # The MCP argument is ONE comma-separated string; the CLI takes repeated
            # `--into`. Split here rather than in the api, so the api keeps the shape
            # that cannot lose a spec containing a comma in its title.
            into=[x.strip() for x in str(a.get("into", "")).split(",") if x.strip()],
            globs=a.get("globs", "") or "",
            needs=a.get("needs", "") or "",
            agent=agent,
        ),
        "payload": ("item", "created"),
    },
    "ddflow_task_add": {
        "description": (
            "Add a task to a phase. ALWAYS set globs to the paths this task will write: "
            "they are what lets two agents work in parallel safely, and an unset glob "
            "means the conflict detector cannot protect you."
        ),
        "properties": {
            "id": ("string", "Short stable id, e.g. 'P2.T1'.", True),
            "phase": ("string", "Owning phase id. Give this OR `parent`.", False),
            "parent": (
                "string",
                "Owning phase id, OR another TASK's id, which makes this a SUB-TASK with its own globs and dependencies, run in parallel with its siblings. Same field as `phase` (the CLI has both names).",
                False,
            ),
            "title": ("string", "One-line description.", False),
            "needs": ("string", "Comma-separated ids this task depends on.", False),
            "globs": ("string", "Comma-separated path globs this task writes.", False),
            "body": ("string", "Detail and acceptance criteria.", False),
            "tags": ("string", "Comma-separated tags.", False),
            "priority": ("integer", "Lower is offered first (default 100).", False),
            "line": (
                "string",
                "Release line this lands on (a name from [flow.lines], or the current "
                "line). Omit for the current line; tasks inherit a phase's line.",
                False,
            ),
            "lines": (
                "string",
                "Release lines a FIX must reach, e.g. '1,2,3': written where [flow].port_strategy says, plus a port task `<id>@<line>` per other line.",
                False,
            ),
            "port_of": (
                "string",
                "Earlier fix this follows up: reuses the lines it reached.",
                False,
            ),
            "readd": (
                "boolean",
                "File a REMOVED item's id again with this definition. An id still in the "
                "queue is always refused -- change that item with ddflow_update.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().task_add(
            repo,
            a["id"],
            title=a.get("title", "") or "",
            parent=a.get("parent") or a.get("phase", "") or "",
            needs=a.get("needs", "") or "",
            globs=a.get("globs", "") or "",
            body=a.get("body", "") or "",
            tags=a.get("tags", "") or "",
            priority=int(a.get("priority") or _api().DEFAULT_PRIORITY),
            line=a.get("line", "") or "",
            lines=a.get("lines", "") or "",
            port_of=a.get("port_of", "") or "",
            readd=bool(a.get("readd")),
            answer=_answer(a),
            agent=agent,
        ),
        "payload": ("id", "line", "ports", "port_strategy", "defaulted"),
    },
}

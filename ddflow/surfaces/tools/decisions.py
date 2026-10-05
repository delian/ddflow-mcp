"""MCP tools: decisions: add, list, applicable, supersede.

One slice of the `TOOLS` registry, assembled in `surfaces/tools/__init__.py`."""

from __future__ import annotations

from typing import Any

from ._common import _answer, _api

TOOLS: dict[str, dict[str, Any]] = {
    "ddflow_decision_add": {
        "description": (
            "Record an architectural decision so the project stays consistent and the reasoning survives: HOW the software is built (a representation, boundary, library, invariant), settled by you or the operator. ALWAYS set `globs` to the code it governs, so it reaches whoever works those files; record `alternatives` too, or they get re-proposed."
        ),
        "properties": {
            "id": (
                "string",
                "Stable id, e.g. 'D1'. Choose one: a generated id cannot be cited in advance.",
                False,
            ),
            "title": ("string", "The decision as a one-line statement.", True),
            "decision": ("string", "What was DECIDED (not what was discussed).", True),
            "context": ("string", "The forces: why a decision was needed at all.", False),
            "consequences": ("string", "What it costs, including what it makes harder.", False),
            "alternatives": ("string", "What was rejected, and why.", False),
            "globs": ("string", "Comma-separated paths this governs.", False),
            "by": ("string", "'operator' or 'agent' or a name.", False),
            "supersedes": ("string", "Comma-separated ids this replaces.", False),
            "item": ("string", "The task it arose from.", False),
            "tags": ("string", "Comma-separated tags.", False),
            "status": (
                "string",
                "proposed | accepted (default) | superseded. 'proposed' is honest about a decision the operator has not ratified.",
                False,
            ),
            "sources": (
                "string",
                "Where this came from, comma-separated: an ADR path, a URL, a commit sha (so an audit can check it exists).",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().decision_add(
            repo,
            _api().decisions.Draft(
                title=a.get("title", ""),
                decision=a.get("decision", ""),
                id=a.get("id", "") or "",
                context=a.get("context", "") or "",
                consequences=a.get("consequences", "") or "",
                alternatives=a.get("alternatives", "") or "",
                globs=a.get("globs", "") or "",
                tags=a.get("tags", "") or "",
                sources=a.get("sources", "") or "",
                status=a.get("status", "") or "accepted",
                by=a.get("by", "") or "",
                item=a.get("item", "") or "",
                supersedes=a.get("supersedes", "") or "",
                answer=_answer(a),
            ),
            agent=agent,
        ),
        # `{"id": "..."}` — what `ddflow decision add --json` has always printed.
        "payload": ("id",),
    },
    "ddflow_decision_list": {
        "description": (
            "Every architectural decision in force. Superseded ones are "
            "hidden unless you ask for them — they are kept, never deleted, "
            "because how the architecture got here is what a rebuild needs."
        ),
        "properties": {
            "all": ("boolean", "Include superseded decisions.", False),
            "since": ("string", "Only those recorded at or after this ISO date.", False),
            "limit": ("integer", "Newest decisions returned (default 25; 0 = all).", False),
        },
        "api": lambda repo, a, agent: _api().decision_list(
            repo,
            all=bool(a.get("all")),
            since=a.get("since", "") or "",
            limit=int(a["limit"]) if a.get("limit") else None,
        ),
        "payload": "rows",
    },
    "ddflow_decision_applicable": {
        "description": (
            "The architectural decisions that govern a specific item's declared files. "
            "CALL THIS BEFORE IMPLEMENTING: it is how a decision reaches the person "
            "writing the code, without them having to know it exists. Returns "
            "project-wide decisions too."
        ),
        "properties": {"id": ("string", "Item id.", True)},
        "api": lambda repo, a, agent: _api().decision_applicable(repo, a["id"]),
        "payload": ("applicable", "project_wide"),
    },
    "ddflow_decision_supersede": {
        "description": (
            "Mark a decision replaced by a newer one. Decisions are never "
            "edited or deleted; a reversal is a new decision that names the "
            "old one."
        ),
        "properties": {
            "id": ("string", "The decision being replaced.", True),
            "by": ("string", "The decision that replaces it.", True),
            "reason": ("string", "Why it changed.", False),
        },
        "api": lambda repo, a, agent: _api().decision_supersede(
            repo, a["id"], by=a.get("by", "") or "", reason=a.get("reason", "") or "", agent=agent
        ),
        "payload": ("id", "by"),
    },
}

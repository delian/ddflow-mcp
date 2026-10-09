"""MCP tools: rebuilding and rendering from the log: replay, render, list, history, import.

One slice of the `TOOLS` registry, assembled in `surfaces/tools/__init__.py`."""

from __future__ import annotations

from typing import Any

from ..declared import reporting as R
from ..declared import setup as ST
from ._common import _api

TOOLS: dict[str, dict[str, Any]] = {
    "ddflow_replay": R.BY_TOOL["ddflow_replay"].tool_entry(),
    "ddflow_render": R.BY_TOOL["ddflow_render"].tool_entry(),
    "ddflow_list": {
        "description": (
            "Read-only lists, newest first, 25 rows unless `limit` (0 = the most: 1000, search 200); a cut says so. "
            "`kind`: task|phase|bug|research|lesson|session|search. Bugs and lessons: the live ones unless `all`/`state`. "
            "History: ddflow_history."
        ),
        "properties": {
            "kind": ("string", "task|phase|bug|research|lesson|session|search", True),
            "id": ("string", "kind=session: one session in full.", False),
            "query": ("string", "kind=search: text to find.", False),
            "state": ("string", "Only this state.", False),
            "phase": ("string", "Only under this phase id.", False),
            "tag": ("string", "Only this tag.", False),
            "owner": ("string", "Only this agent's rows.", False),
            "since": ("string", "Changed at/after this ISO date.", False),
            "item": ("string", "kind=bug: only bugs against this item.", False),
            "limit": ("integer", "Rows (default 25; 0 = the most: 1000, search 200).", False),
            "all": ("boolean", "kind=bug|lesson: include fixed/invalid/superseded.", False),
            "mode": ("string", "search: ranked|exact|regex.", False),
            "sources": ("string", "search: comma-separated record kinds.", False),
        },
        "api": lambda repo, a, agent: _api().view_read(
            repo,
            a["kind"],
            id=a.get("id", "") or "",
            query=a.get("query", "") or "",
            state=a.get("state", "") or "",
            phase=a.get("phase", "") or "",
            tag=a.get("tag", "") or "",
            owner=a.get("owner", "") or "",
            since=a.get("since", "") or "",
            item=a.get("item", "") or "",
            limit=1000 if a.get("limit") == 0 else int(a.get("limit") or 25),
            all=bool(a.get("all")),
            mode=a.get("mode", "") or "ranked",
            sources=a.get("sources", "") or "",
        ),
        "payload": "",
    },
    "ddflow_rebuild": R.BY_TOOL["ddflow_rebuild"].tool_entry(),
    "ddflow_history": R.BY_TOOL["ddflow_history"].tool_entry(),
    "ddflow_import": ST.BY_TOOL["ddflow_import"].tool_entry(),
}

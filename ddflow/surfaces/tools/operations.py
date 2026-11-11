"""MCP tools: list (hand-written; the rest are declared).

Hand-written entries merged into `TOOLS` in `surfaces/tools/__init__.py`."""

from __future__ import annotations

from typing import Any

from ...core import limits as L
from ._common import _api


def _limit(a: dict[str, Any]) -> int:
    """`limit` as given; 0 is the most a kind gives. Unset: the most for `all` and for phases
    (few, and a page hid most of them: Bd515797884, as the CLI), else 25."""
    if a.get("limit") == 0 or (
        a.get("limit") is None and (a.get("all") or a.get("kind") == "phase")
    ):
        return L.SEARCH_MAX_LIMIT if a.get("kind") == "search" else L.LIST_MAX_LIMIT
    return int(a.get("limit") or 25)


TOOLS: dict[str, dict[str, Any]] = {
    "ddflow_list": {
        "description": (
            "Read-only lists, newest first, 25 rows unless `limit` "
            f"(0 = the most: {L.LIST_MAX_LIMIT}, search {L.SEARCH_MAX_LIMIT}); a cut says so. "
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
            "match": ("string", "Only rows whose id/title contains this.", False),
            "limit": (
                "integer",
                f"Rows (default 25; 0 = the most: {L.LIST_MAX_LIMIT}, search {L.SEARCH_MAX_LIMIT}).",
                False,
            ),
            "offset": ("integer", "Skip this many rows (paging; a cut list says `next`).", False),
            "all": ("boolean", "Every row (up to the most); bug|lesson also closed ones.", False),
            "mode": ("string", "search: ranked|exact|regex.", False),
            "sources": ("string", "search: comma-separated record kinds.", False),
            "source": ("string", "search: comma-separated search sources (default all).", False),
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
            match=a.get("match", "") or "",
            limit=_limit(a),
            offset=int(a.get("offset") or 0),
            all=bool(a.get("all")),
            mode=a.get("mode", "") or "ranked",
            sources=a.get("sources", "") or "",
            source=a.get("source", "") or "",
        ),
        "payload": "",
    },
}

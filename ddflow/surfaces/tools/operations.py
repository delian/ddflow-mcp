"""MCP tools: rebuilding and rendering from the log: replay, render, list, history, import.

One slice of the `TOOLS` registry, assembled in `surfaces/tools/__init__.py`."""

from __future__ import annotations

from typing import Any

from ..declared import setup as ST
from ._common import _api

TOOLS: dict[str, dict[str, Any]] = {
    "ddflow_replay": {
        "description": (
            "Reconstruct the project's whole decision history from the log: every "
            "operator prompt in order, every architectural decision, every research "
            "verdict, every lesson, and the shape of the queue. This is what rebuilds "
            "the project if the code is lost — it reproduces the DECISIONS, not the "
            "bytes."
        ),
        "properties": {
            "out": ("string", "Write a recovery kit to this directory.", False),
            "verify": (
                "boolean",
                "Re-resolve every recorded commit sha against this repository and report the ones that are gone: a reconstruction citing unresolvable shas is a narrative, not a record.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().replay(
            repo, out_dir=a.get("out", "") or "", verify=bool(a.get("verify")), agent=agent
        ),
        "payload": "text",
        "text": True,
        "kind": "replay",
    },
    "ddflow_render": {
        "description": (
            "Regenerate the human-readable markdown views (queue, lessons, the "
            "one-paragraph lessons summary, research) under docs/ddflow/."
        ),
        "properties": {
            "out": ("string", "Directory for the generated views (default: docs/ddflow).", False),
            "show": (
                "string",
                "Print ONE view instead of writing files: lessons, lessons-summary, "
                "research, or board. "
                "This is what the ddflow:// resources are served from.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().render(
            repo,
            show=a.get("show", "") or "",
            out_dir=a.get("out") or _api().DEFAULT_RENDER_DIR,
            agent=agent,
        ),
        # Two shapes, both pre-existing: `--show` returned the DOCUMENT and without it
        # the answer was the list of files written. A predicate, because which one it
        # is cannot be known until the call.
        "payload": lambda a: "text" if a.get("show") else ("files",),
        "text": lambda a: bool(a.get("show")),
        "kind": "render",
    },
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
    "ddflow_rebuild": {
        "description": (
            "Re-derive the search index from the event log. The index is a "
            "disposable cache; this is never a data-loss operation."
        ),
        "properties": {},
        "api": lambda repo, a, agent: _api().rebuild(repo, agent=agent),
        "payload": ("events", "items"),
    },
    "ddflow_history": {
        "description": (
            "ONE timeline of everything that happened: claims, releases, gates, bugs, "
            "decisions, lessons, completions. Other views say what is true now; this says "
            "how it got that way.\n\n"
            "Filter with `item` (one task's life), `kind` (a family: 'gate', "
            "'lease.acquired', 'decision,bug'), `since`, `by_agent`. Exit 2 means nothing "
            "matched: an answer, not a failure."
        ),
        "properties": {
            "item": ("string", "Restrict to one item's timeline.", False),
            "kind": (
                "string",
                "Comma-separated event kinds or families: 'gate', 'lease.acquired', "
                "'decision,bug'.",
                False,
            ),
            "since": ("string", "ISO timestamp lower bound.", False),
            "limit": ("integer", "Most recent N entries (default 40).", False),
            "tail": ("integer", "Last N entries, oldest first (overrides limit).", False),
            "by_agent": ("string", "Only this agent's events.", False),
        },
        "api": lambda repo, a, agent: _api().history(
            repo,
            item=a.get("item", "") or "",
            kind=a.get("kind", "") or "",
            since=a.get("since", "") or "",
            limit=int(a.get("limit") or 40),
            agent=agent,
            by_agent=a.get("by_agent", "") or "",
            tail=int(a.get("tail") or 0),
        ),
        "payload": ("total", "shown", "events"),
    },
    "ddflow_import": ST.BY_TOOL["ddflow_import"].tool_entry(),
}

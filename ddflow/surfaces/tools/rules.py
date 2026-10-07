"""MCP tools: project rules.

One slice of the `TOOLS` registry, assembled in `surfaces/tools/__init__.py`."""

from __future__ import annotations

from typing import Any

from ._common import _api, _list_or_none


def _rule_answer(a: dict[str, Any], relations: tuple[str, ...]) -> Any:
    """The duplicate-check answer a rule tool call carries, or None. More than one is
    refused (the CLI's flags are mutually exclusive; silently keeping one dropped the
    other, rubber-duck)."""
    given = [r for r in relations if (a.get(r) if r != "new" else bool(a.get("new")))]
    if len(given) > 1:
        raise ValueError(f"answer one of {', '.join(given)}, not several")
    if not given:
        return None
    rel = given[0]
    return _api().RuleDedupAnswer(rel, "" if rel == "new" else a[rel])


TOOLS: dict[str, dict[str, Any]] = {
    "ddflow_rule_add": {
        "description": (
            "Add a project rule; duplicate-checked like every add (answer new | extends:ID | duplicate_of:ID | related:ID)."
        ),
        "properties": {
            "id": ("string", "Rule id, e.g. r-naming.", True),
            "title": ("string", "One-line rule statement.", True),
            "content": ("string", "Rule text (may be empty for check_only).", False),
            "tags": ("string", "Comma-separated tags.", False),
            "scope": ("string", "project (default) | phase | task | global.", False),
            "priority": ("integer", "Priority 0-100 (default 50).", False),
            "globs": ("string", "Comma-separated globs it governs.", False),
            "new": ("boolean", "Dedup answer: a different record.", False),
            "extends": ("string", "Dedup answer: extends record ID.", False),
            "duplicate_of": (
                "string",
                "Dedup answer: same as record ID.",
                False,
            ),
            "related": ("string", "Dedup answer: related to ID.", False),
            "check": (
                "boolean",
                "Dry run: list duplicates only.",
                False,
            ),
        },
        "api": lambda repo, a, agent: (
            _api().rule_dedup_check_dry_run(
                repo,
                a.get("content", "") or "",
                title=a.get("title", "") or "",
                rule_id=a.get("id", "") or "",
                agent=agent,
            )
            if bool(a.get("check"))
            else _api().rule_add(
                repo,
                _api().Rule(
                    id=a["id"],
                    title=a["title"],
                    content=a.get("content", "") or "",
                    tags=_list_or_none(a, "tags") or [],
                    scope=a.get("scope", "project") or "project",
                    priority=int(a.get("priority", 50) or 50),
                    globs=_list_or_none(a, "globs") or [],
                ),
                agent=agent,
                check_dedup=True,
                dedup_answer=_rule_answer(a, ("new", "extends", "duplicate_of", "related")),
            )
        ),
        "payload": (
            "id",
            "candidates",
            "related",
            "options",
            "extended",
            "extended_kind",
            "relation",
            "dedupe_unavailable",
        ),
    },
    "ddflow_rule_list": {
        "description": (
            "List the project's rules, filtered by tag or scope: what governs the current work."
        ),
        "properties": {
            "tag": ("string", "Filter by this tag.", False),
            "scope": ("string", "Filter by this scope.", False),
            "json": ("boolean", "", False),
            "limit": ("integer", "", False),
        },
        "deprecated": {
            "json": "the result is always JSON",
            "limit": "the list is not truncated",
        },
        "api": lambda repo, a, agent: _api().rule_list(
            repo,
            tag=a.get("tag") or None,
            scope=a.get("scope") or None,
        ),
        "payload": ("rows", "count"),
    },
    "ddflow_rule_search": {
        "description": (
            "Search rules by title or content, ranked by relevance, for an area or topic."
        ),
        "properties": {
            "query": ("string", "Search query (keywords or regex).", True),
            "exact": ("boolean", "Exact phrase.", False),
            "regex": ("boolean", "Query is a regex.", False),
            "tag": ("string", "Filter results by this tag.", False),
            "scope": ("string", "Filter results by this scope.", False),
            "limit": ("integer", "Maximum results to return (default 10).", False),
        },
        "api": lambda repo, a, agent: _api().rule_search(
            repo,
            a["query"],
            limit=int(a.get("limit", 10) or 10),
            exact=bool(a.get("exact")),
            regex=bool(a.get("regex")),
            tag=a.get("tag") or None,
            scope=a.get("scope") or None,
        ),
        "payload": ("rows", "count", "query"),
    },
    "ddflow_rule_edit": {
        "description": (
            "Change fields of an existing rule; omitted fields stay. Recorded in the manifest; "
            "new text is duplicate-checked."
        ),
        "properties": {
            "id": ("string", "Rule id to edit.", True),
            "title": ("string", "New title.", False),
            "content": ("string", "New content.", False),
            "tags": ("string", "Comma-separated tags.", False),
            "scope": ("string", "New scope.", False),
            "priority": ("integer", "New priority.", False),
            "globs": ("string", "Comma-separated globs.", False),
            "new": ("boolean", "Answer: different.", False),
            "related": ("string", "Answer: related ID.", False),
        },
        "api": lambda repo, a, agent: _api().rule_update(
            repo,
            a["id"],
            dedup_answer=_rule_answer(a, ("new", "related")),
            agent=agent,
            **(
                {
                    "title": a["title"],
                }
                if a.get("title")
                else {}
            ),
            **(
                {
                    "content": a["content"],
                }
                if a.get("content")
                else {}
            ),
            **(
                {
                    "tags": _list_or_none(a, "tags") or [],
                }
                if "tags" in a
                else {}
            ),
            **(
                {
                    "scope": a["scope"],
                }
                if a.get("scope")
                else {}
            ),
            **(
                {
                    "priority": int(a.get("priority") or 50),
                }
                if a.get("priority") is not None
                else {}
            ),
            **(
                {
                    "globs": _list_or_none(a, "globs") or [],
                }
                if "globs" in a
                else {}
            ),
        ),
        "payload": ("id", "candidates", "related", "options", "dedupe_unavailable"),
    },
    "ddflow_rule_remove": {
        "description": (
            "Delete a rule and regenerate the DDFLOW.md manifest. Rules are files, not "
            "log records: the removal leaves no record and takes no reason."
        ),
        "properties": {
            "id": ("string", "Rule id to remove.", True),
            "reason": ("string", "", False),
        },
        "deprecated": {"reason": "a removal takes no reason and leaves no record"},
        "api": lambda repo, a, agent: _api().rule_remove(
            repo,
            a["id"],
        ),
        "payload": ("id",),
    },
    "ddflow_rule_show": {
        "description": (
            "One rule with all its metadata: title, content, tags, scope, priority, globs, timestamps."
        ),
        "properties": {
            "id": ("string", "Rule id to retrieve.", True),
        },
        "api": lambda repo, a, agent: _api().rule_get(
            repo,
            a["id"],
        ),
        "payload": (
            "id",
            "title",
            "content",
            "tags",
            "scope",
            "priority",
            "globs",
            "created",
            "updated",
        ),
    },
}

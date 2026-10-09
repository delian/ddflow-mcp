"""The project-rule commands, declared once: rule add, edit, list, search, show, remove, sync.

`surfaces/parsers/rules.py` registers their command-line half and `surfaces/tools/rules.py`
takes their MCP entries (D-unify 4, B-uni-cmd-migrate.6-rest). `ddflow rule` alone lists them.
"""

from __future__ import annotations

from ..registry import Command, Param, by_tool
from ..tools._common import _api, _list_or_none, _rule_answer

COMMANDS: tuple[Command, ...] = (
    Command(
        path=("rule", "add"),
        tool="ddflow_rule_add",
        description="Add a project rule; duplicate-checked like every add (answer new | extends:ID | duplicate_of:ID | related:ID).",
        call=lambda repo, a, agent: (
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
                    priority=50 if a.get("priority") is None else int(a["priority"]),
                    globs=_list_or_none(a, "globs") or [],
                ),
                agent=agent,
                check_dedup=True,
                dedup_answer=_rule_answer(a, ("new", "extends", "duplicate_of", "related")),
            )
        ),
        payload=(
            "id",
            "candidates",
            "related",
            "options",
            "extended",
            "extended_kind",
            "relation",
            "dedupe_unavailable",
        ),
        params=(
            Param("id", help="Rule id, e.g. r-naming.", cli_help="", required=True),
            Param("title", help="One-line rule statement.", cli_help="", required=True),
            Param(
                "content", help="Rule text (may be empty for check_only).", cli_help="", default=""
            ),
            Param("tags", help="Comma-separated tags.", cli_help="", default=""),
            Param(
                "scope",
                help="project (default) | phase | task | global.",
                cli_help="",
                default="project",
            ),
            Param("priority", type="integer", help="Priority 0-100 (default 50).", cli_help=""),
            Param("globs", help="Comma-separated globs it governs.", cli_help="", default=""),
            Param(
                "new",
                type="boolean",
                help="Dedup answer: a different record.",
                cli_help="a different rule, file it",
                exclusive="answer",
            ),
            Param(
                "extends",
                help="Dedup answer: extends record ID.",
                cli_help="",
                default="",
                metavar="ID",
                exclusive="answer",
            ),
            Param(
                "duplicate_of",
                help="Dedup answer: same as record ID.",
                cli_help="",
                default="",
                metavar="ID",
                exclusive="answer",
            ),
            Param(
                "related",
                help="Dedup answer: related to ID.",
                cli_help="",
                default="",
                metavar="ID",
                exclusive="answer",
            ),
            Param(
                "check",
                type="boolean",
                help="Dry run: list duplicates only.",
                cli_help="dry run: list duplicates only",
                exclusive="answer",
            ),
        ),
    ),
    Command(
        path=("rule", "edit"),
        tool="ddflow_rule_edit",
        description="Change fields of an existing rule; omitted fields stay. Recorded in the manifest "
        "and, as a def.updated, in the log; new text is duplicate-checked.",
        call=lambda repo, a, agent: _api().rule_update(
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
                    "priority": int(a["priority"]),
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
        payload=("id", "candidates", "related", "options", "dedupe_unavailable"),
        params=(
            Param("id", help="Rule id to edit.", cli_help="", positional=True),
            Param("title", help="New title.", cli_help=""),
            Param("content", help="New content.", cli_help=""),
            Param("tags", help="Comma-separated tags.", cli_help=""),
            Param("scope", help="New scope.", cli_help=""),
            Param("globs", help="Comma-separated globs.", cli_help=""),
            Param("priority", type="integer", help="New priority.", cli_help=""),
            Param(
                "new",
                type="boolean",
                help="Answer: different.",
                cli_help="answer the duplicate check: different",
                exclusive="answer",
            ),
            Param(
                "related",
                help="Answer: related ID.",
                cli_help="answer it: related to ID",
                default="",
                metavar="ID",
                exclusive="answer",
            ),
        ),
        tool_order=(
            "id",
            "title",
            "content",
            "tags",
            "scope",
            "priority",
            "globs",
            "new",
            "related",
        ),
    ),
    Command(
        path=("rule", "list"),
        tool="ddflow_rule_list",
        deprecated={"json": "the result is always JSON", "limit": "the list is not truncated"},
        description="List the project's rules, filtered by tag or scope: what governs the current work.",
        call=lambda repo, a, agent: _api().rule_list(
            repo,
            tag=a.get("tag") or None,
            scope=a.get("scope") or None,
        ),
        payload=("rows", "count"),
        params=(
            Param("tag", help="Filter by this tag.", cli_help="", default=""),
            Param("scope", help="Filter by this scope.", cli_help="", default=""),
            Param("json", type="boolean", help="", mcp_only=True),
            Param("limit", type="integer", help="", mcp_only=True),
        ),
    ),
    Command(
        path=("rule", "search"),
        tool="ddflow_rule_search",
        description="Search rules by title or content, ranked by relevance, for an area or topic.",
        call=lambda repo, a, agent: _api().rule_search(
            repo,
            a["query"],
            limit=10 if a.get("limit") is None else int(a["limit"]),
            exact=bool(a.get("exact")),
            regex=bool(a.get("regex")),
            tag=a.get("tag") or None,
            scope=a.get("scope") or None,
        ),
        payload=("rows", "count", "query"),
        params=(
            Param("query", help="Search query (keywords or regex).", cli_help="", positional=True),
            Param(
                "limit",
                type="integer",
                help="Maximum results to return (default 10).",
                cli_help="",
                default=10,
            ),
            Param("exact", type="boolean", help="Exact phrase.", cli_help=""),
            Param("regex", type="boolean", help="Query is a regex.", cli_help=""),
            Param("tag", help="Filter results by this tag.", cli_help="", default=""),
            Param("scope", help="Filter results by this scope.", cli_help="", default=""),
        ),
        tool_order=("query", "exact", "regex", "tag", "scope", "limit"),
    ),
    Command(
        path=("rule", "show"),
        tool="ddflow_rule_show",
        description="One rule with all its metadata: title, content, tags, scope, priority, globs, timestamps.",
        call=lambda repo, a, agent: _api().rule_get(
            repo,
            a["id"],
        ),
        payload=(
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
        params=(Param("id", help="Rule id to retrieve.", cli_help="", positional=True),),
    ),
    Command(
        path=("rule", "remove"),
        tool="ddflow_rule_remove",
        deprecated={"reason": "a removal takes no reason"},
        description="Delete a rule file and regenerate the DDFLOW.md manifest. The rule's definition "
        "in the log is retired (def.retired; its history stays); a retirement the log "
        "cannot take fails. A removal takes no reason.",
        call=lambda repo, a, agent: _api().rule_remove(repo, a["id"], agent=agent),
        payload=("id",),
        params=(
            Param("id", help="Rule id to remove.", cli_help="", positional=True),
            Param("reason", help="", mcp_only=True),
        ),
    ),
    Command(
        path=("rule", "sync"),
        summary="record hand-edited rule files in the log; write the files the log has and the disk lacks",
        tool="ddflow_rule_sync",
        description="Sync rule files with the log: record hand edits and new files, restore missing ones.",
        call=lambda repo, a, agent: _api().rule_sync(repo, agent=agent),
        payload=("recorded", "updated", "restored", "failed"),
        params=(),
    ),
)


BY_TOOL = by_tool(COMMANDS)

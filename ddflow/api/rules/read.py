"""`rule get` / `list` / `search` / `sync`: read rules, and make the log match the files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...core import outcome as O
from ...services.guidance import deffields as DF
from ...services.guidance import ruleview as RV
from ...services.guidance.kinds import RULE
from ...services.rules import RulesStorage
from ...services.searchcore import SearchError, check_regex
from .._base import _load
from ..defs import def_record_unchecked, def_update


def rule_get(repo: Path, rule_id: str) -> O.Outcome:
    """Retrieve a single rule.

    Args:
        repo: Path to the repository root
        rule_id: The rule ID to retrieve

    Returns:
        Outcome with the rule data
    """
    storage = RulesStorage(repo)

    try:
        rule = storage.get(rule_id)

        # Handle timestamps that might be datetime or string
        created_str = (
            rule.created.isoformat() if hasattr(rule.created, "isoformat") else str(rule.created)
        )
        updated_str = (
            rule.updated.isoformat() if hasattr(rule.updated, "isoformat") else str(rule.updated)
        )

        return O.ok(
            "rule.show",
            id=rule.id,
            title=rule.title,
            content=rule.content,
            tags=rule.tags,
            scope=rule.scope,
            priority=rule.priority,
            globs=rule.globs,
            enforcement=rule.enforcement,
            created=created_str,
            updated=updated_str,
        )

    except FileNotFoundError:
        return O.failed("rule.show", f"Rule {rule_id} not found", id=rule_id)
    except Exception as exc:
        return O.failed("rule.show", f"Failed to retrieve rule: {exc}", id=rule_id)


def rule_list(repo: Path, tag: str | None = None, scope: str | None = None) -> O.Outcome:
    """List all rules, optionally filtered.

    Args:
        repo: Path to the repository root
        tag: Optional tag to filter by
        scope: Optional scope to filter by

    Returns:
        Outcome with list of rules
    """
    storage = RulesStorage(repo)

    try:
        rules = storage.list(tag=tag, scope=scope)
        rows = [
            {
                "id": r.id,
                "title": r.title,
                "scope": r.scope,
                "tags": r.tags,
                "priority": r.priority,
            }
            for r in sorted(rules, key=lambda r: r.id)
        ]

        data: dict[str, Any] = {"rows": rows, "count": len(rows)}
        if tag:
            data["tag"] = tag
        if scope:
            data["scope"] = scope

        if not rows:
            reason = ""
            if tag:
                reason = f" with tag '{tag}'"
            if scope:
                reason += f" in scope '{scope}'"
            return O.nothing(
                "rule.list",
                f"No rules found{reason}.",
                **data,
            )

        return O.ok("rule.list", **data)

    except Exception as exc:
        return O.failed("rule.list", f"Failed to list rules: {exc}")


def rule_search(
    repo: Path,
    query: str,
    *,
    limit: int = 10,
    exact: bool = False,
    regex: bool = False,
    tag: str | None = None,
    scope: str | None = None,
) -> O.Outcome:
    """Search for rules by content or title.

    Uses substring and TF-IDF-like similarity scoring to rank results.
    Higher scores for substring matches, lower for token-based similarity.

    Args:
        repo: Path to the repository root
        query: Search query text
        limit: Maximum number of results to return
        exact: If True, search for exact phrase match
        regex: If True, treat query as a regular expression
        tag: Optional tag to filter by
        scope: Optional scope to filter by

    Returns:
        Outcome with ranked list of matching rules
    """
    storage = RulesStorage(repo)

    rx = None
    if regex and not exact:
        try:
            # The one regex safety check every search shares: a pattern that could take
            # exponential time, or does not compile, is refused with the reason.
            rx = check_regex(query)
        except SearchError as exc:
            return O.refused("rule.search", str(exc), query=query)

    try:
        rules = storage.list(tag=tag, scope=scope)

        # Score each rule
        scored_rules = []
        query_lower = query.lower()

        for rule in rules:
            # Combine title and content for scoring
            combined = f"{rule.title} {rule.content}".lower()

            score = 0.0
            if exact:
                # Exact phrase match
                if query_lower in combined:
                    score = 1.0
            elif rx is not None:
                if rx.search(combined):
                    score = 0.8
            # Default: substring match with fallback to similarity scoring
            # First check for substring match (higher score)
            elif query_lower in combined:
                score = 1.0
            else:
                # Fallback: TF-IDF-like similarity scoring
                score = rule.similarity_score(query)

            if score > 0:
                scored_rules.append((rule, score))

        # Sort by score descending, then by id for stability
        scored_rules.sort(key=lambda x: (-x[1], x[0].id))

        # Limit results
        scored_rules = scored_rules[:limit]

        if not scored_rules:
            return O.nothing(
                "rule.search",
                f"No rules found matching '{query}'",
                query=query,
                rows=[],
                count=0,
            )

        rows = [
            {
                "id": r.id,
                "title": r.title,
                "scope": r.scope,
                "tags": r.tags,
                "priority": r.priority,
                "score": score,
            }
            for r, score in scored_rules
        ]

        return O.ok(
            "rule.search",
            rows=rows,
            count=len(rows),
            query=query,
        )

    except Exception as exc:
        return O.failed("rule.search", f"Failed to search rules: {exc}")


def rule_sync(repo: Path, *, agent: str = "") -> O.Outcome:
    """Make the log say what the rule files say, and the files exist for what the log says.

    The files are a generated view of the rule definitions (D-unify 7). A rule file with no
    record is recorded (``def.recorded``, provenance ``via = sync``); a file whose content
    differs from its live record -- a hand edit -- is recorded as ``def.updated``; a live
    record with no file has its file written from the log. A file already recorded, a retired
    rule and a file that does not load are left as they are. Fails (exit 1) when an event could
    not be appended; what was done before stays done, and a second call finishes the rest."""
    _log, cfg, st = _load(repo, agent)
    recorded: list[str] = []
    updated: list[str] = []
    failed: list[dict[str, str]] = []
    for how, rec in RV.edits(repo, cfg, st):
        fields = DF.to_fields(rec, RULE)
        prov = {"via": "sync", **DF.to_provenance(rec)}
        try:
            write = def_record_unchecked if how == RV.RECORD else def_update
            res = write(
                repo,
                RULE.kind,
                rec.id,
                fields,
                source=RV.relpath(repo, rec.id),
                provenance=prov,
                agent=agent,
            )
        except Exception as exc:
            failed.append({"id": rec.id, "why": str(exc)})
            continue
        if res.exit in (O.OK, O.NOTHING):
            (recorded if how == RV.RECORD else updated).append(rec.id)
        else:
            failed.append({"id": rec.id, "why": res.reason})
    try:
        restored = RV.restore(repo, _load(repo, agent)[2])
    except (OSError, ValueError) as exc:
        failed.append({"id": "(files)", "why": f"could not write a rule file from the log: {exc}"})
        restored = []
    data = {"recorded": recorded, "updated": updated, "restored": restored, "failed": failed}
    if failed:
        return O.failed(
            "rule.sync",
            "could not record: " + "; ".join(f"{f['id']} ({f['why']})" for f in failed),
            **data,
        )
    return O.ok("rule.sync", **data)

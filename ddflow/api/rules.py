"""Rules API: persistence, manifest generation, and orchestration.

This module provides high-level operations for managing project rules,
including manifest generation and integration with the event log.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import Config
from ..core import outcome as O
from ..core.ids import auto_id
from ..core.model import State
from ..infra.log import EventLog
from ..services.rules import Rule, RulesStorage
from ._base import _load


def rules_manifest(storage: RulesStorage) -> str:
    """Generate DDFLOW.md manifest from stored rules.

    The manifest is auto-generated and organized by scope, then by tag.
    Never hand-edited (marked in header).

    Args:
        storage: RulesStorage instance with loaded rules

    Returns:
        Markdown content for DDFLOW.md
    """
    rules = storage.list()

    if not rules:
        return """# Project Rules

This file is auto-generated from `.ddflow/rules/` — do not hand-edit it.

No project rules are currently defined.
"""

    # Group rules by scope, then by tag
    by_scope: dict[str, dict[str, list[Rule]]] = {}

    for rule in sorted(rules, key=lambda r: (r.scope, r.tags or [""], r.id)):
        scope = rule.scope
        if scope not in by_scope:
            by_scope[scope] = {}

        # Use the first tag as the primary category, or "general" if no tags
        tag = rule.tags[0] if rule.tags else "general"
        if tag not in by_scope[scope]:
            by_scope[scope][tag] = []

        by_scope[scope][tag].append(rule)

    # Build markdown
    lines = [
        "# Project Rules",
        "",
        "This file is auto-generated from `.ddflow/rules/` — do not hand-edit it.",
        "",
    ]

    for scope in sorted(by_scope.keys()):
        lines.append(f"## {scope.title()} Scope")
        lines.append("")

        for tag in sorted(by_scope[scope].keys()):
            lines.append(f"### {tag.title()}")
            lines.append("")

            for rule in sorted(by_scope[scope][tag], key=lambda r: r.id):
                # Short format: rule ID and title
                lines.append(f"- **{rule.id}**: {rule.title}")

                # If there are globs, show them
                if rule.globs:
                    globs_str = ", ".join(rule.globs)
                    lines.append(f"  - Globs: {globs_str}")

                # Show priority if non-default (50)
                if rule.priority != 50:
                    lines.append(f"  - Priority: {rule.priority}")

            lines.append("")

    return "\n".join(lines)


def apply_rule_update(
    repo: Path, rule: Rule, *, agent: str = "", operation: str = "created"
) -> O.Outcome:
    """Add or update a rule and write the manifest.

    Orchestrates RulesStorage operations with event logging.

    Args:
        repo: Path to the repository root
        rule: The Rule to add or update
        agent: Agent ID for event logging
        operation: Event kind: "created", "updated"

    Returns:
        Outcome with rule_id and other details
    """
    log, cfg, st = _load(repo, agent)
    storage = RulesStorage(repo)

    try:
        # Determine if we're adding or updating
        existing = None
        try:
            existing = storage.get(rule.id)
        except FileNotFoundError:
            pass

        if existing and operation == "created":
            return O.failed(
                f"rule.{operation}",
                f"Rule {rule.id} already exists; use operation='updated' to modify it",
                id=rule.id,
            )

        # Perform the storage operation
        if existing:
            rule, event_fields = storage.update(rule.id, **{
                "title": rule.title,
                "content": rule.content,
                "tags": rule.tags,
                "scope": rule.scope,
                "priority": rule.priority,
                "globs": rule.globs,
            })
        else:
            rule, event_fields = storage.add(rule)

        # Record the event
        event_kind = f"rule.{operation}"
        with log.transaction():
            log.append(event_kind, rule.id, event_fields)
            # Update the manifest
            manifest_content = rules_manifest(storage)
            manifest_path = repo / "DDFLOW.md"
            manifest_path.write_text(manifest_content)

        return O.ok(
            event_kind,
            id=rule.id,
            title=rule.title,
            scope=rule.scope,
        )

    except Exception as exc:
        return O.failed(
            f"rule.{operation}",
            f"Failed to {operation} rule: {exc}",
            id=rule.id,
        )


def rule_add(repo: Path, rule: Rule, *, agent: str = "") -> O.Outcome:
    """Add a new rule to the project.

    Args:
        repo: Path to the repository root
        rule: The Rule to add
        agent: Agent ID for event logging

    Returns:
        Outcome with rule_id and other details
    """
    return apply_rule_update(repo, rule, agent=agent, operation="created")


def rule_update(repo: Path, rule_id: str, **fields: Any) -> O.Outcome:
    """Update an existing rule.

    Args:
        repo: Path to the repository root
        rule_id: The rule ID to update
        **fields: Fields to update

    Returns:
        Outcome indicating success or failure
    """
    log, cfg, st = _load(repo)
    storage = RulesStorage(repo)

    try:
        rule, event_fields = storage.update(rule_id, **fields)

        # Record the event
        with log.transaction():
            log.append("rule.updated", rule_id, event_fields)
            # Update the manifest
            manifest_content = rules_manifest(storage)
            manifest_path = repo / "DDFLOW.md"
            manifest_path.write_text(manifest_content)

        return O.ok(
            "rule.updated",
            id=rule_id,
            **fields,
        )

    except FileNotFoundError:
        return O.failed("rule.updated", f"Rule {rule_id} not found", id=rule_id)
    except Exception as exc:
        return O.failed("rule.updated", f"Failed to update rule: {exc}", id=rule_id)


def rule_remove(repo: Path, rule_id: str) -> O.Outcome:
    """Remove a rule from the project.

    Args:
        repo: Path to the repository root
        rule_id: The rule ID to remove

    Returns:
        Outcome indicating success or failure
    """
    log, cfg, st = _load(repo)
    storage = RulesStorage(repo)

    try:
        event_fields = storage.remove(rule_id)

        # Record the event
        with log.transaction():
            log.append("rule.deleted", rule_id, event_fields)
            # Update the manifest
            manifest_content = rules_manifest(storage)
            manifest_path = repo / "DDFLOW.md"
            manifest_path.write_text(manifest_content)

        return O.ok("rule.deleted", id=rule_id)

    except FileNotFoundError:
        return O.failed("rule.deleted", f"Rule {rule_id} not found", id=rule_id)
    except Exception as exc:
        return O.failed("rule.deleted", f"Failed to delete rule: {exc}", id=rule_id)


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
        return O.ok(
            "rule.show",
            id=rule.id,
            title=rule.title,
            content=rule.content,
            tags=rule.tags,
            scope=rule.scope,
            priority=rule.priority,
            globs=rule.globs,
            created=rule.created.isoformat(),
            updated=rule.updated.isoformat(),
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
                reason += f" in scope '{scope}'" if reason else f" in scope '{scope}'"
            return O.nothing(
                "rule.list",
                f"No rules found{reason}.",
                **data,
            )

        return O.ok("rule.list", **data)

    except Exception as exc:
        return O.failed("rule.list", f"Failed to list rules: {exc}")

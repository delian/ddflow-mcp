"""Rules API: persistence, manifest generation, and orchestration.

This module provides high-level operations for managing project rules,
including manifest generation and integration with the event log.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import ddflow.api._dedupe as DD

from ..core import outcome as O
from ..services.rules import Rule, RulesStorage
from ._base import _load

DEFAULT_RULE_PRIORITY = 50


@dataclass(frozen=True)
class RuleDedupAnswer:
    """Response to a rule dedup check: new, extends, or duplicate_of."""

    relation: str = ""  # "new", "extends", "duplicate_of"
    target: str = ""  # The rule ID to point at

    @classmethod
    def parse(cls, spec: str) -> RuleDedupAnswer:
        """Parse 'new', 'extends ID', 'duplicate_of ID', or 'duplicate ID'."""
        words = (spec or "").replace(":", " ").replace("=", " ").split()
        if not words:
            return cls()
        rel = {"duplicate": "duplicate_of", "dup": "duplicate_of"}.get(words[0], words[0])
        return cls(rel, " ".join(words[1:]))

    def __bool__(self) -> bool:
        return bool(self.relation)

    @property
    def problem(self) -> str:
        """Validate the answer."""
        if not self.relation:
            return ""
        if self.relation not in ("new", "extends", "duplicate_of", "related"):
            return f"unknown answer {self.relation!r}: one of new, extends, duplicate_of, related"
        if self.relation == "new" and self.target:
            return "'new' names no record"
        if self.relation != "new" and not self.target:
            return f"{self.relation!r} needs the id of the rule it points at"
        return ""


def _cross_kind(
    repo: Path, rule: Rule, agent: str, answer: Any = None, *, event_kind: str = "rule.added"
) -> Any:
    """The add-time check of ``rule`` against every OTHER record kind -- decisions,
    lessons, research, tasks, bugs, memories (D-rule-dedupe-everywhere): `check_add`
    itself, with its refusal, its candidates and its answers. None while ``rule`` is not
    in ``[dedupe].kinds``. The other rules are compared by `rule_dedup_check`: rules are
    files, so the index this check reads holds none of them."""

    log, cfg, st = _load(repo, agent)
    if "rule" not in cfg.dedupe.kinds:
        return None
    rec = DD.Record(
        kind="rule", event_kind=event_kind, rid=rule.id, title=rule.title, body=rule.content
    )
    return DD.check_add(repo, log, cfg, st, rec, answer)


def _is_rule(repo: Path, rid: str) -> bool:
    try:
        RulesStorage(repo).get(rid)
    except FileNotFoundError:
        return False
    return True


def _answer_other_kind(
    repo: Path, rule: Rule, answer: RuleDedupAnswer, agent: str
) -> O.Outcome | None:
    """An answer that points at a decision, lesson, task... rather than a rule. None
    when the cross-kind check is off. ``extends`` / ``duplicate_of`` an open record put
    the rule's text on that record (`record.extended`) and file no rule, as every other
    add does; onto a closed or claimed one, and ``related``, the rule is filed and the
    result names the record (a rule is a file: it carries no link in the log)."""

    if not DD.kind_of(_load(repo, agent)[2], answer.target):
        return O.failed(
            "rule.added",
            f"Target {answer.target} not found: no rule or record has that id",
            id=rule.id,
        )
    chk = _cross_kind(repo, rule, agent, DD.Answer(answer.relation, answer.target))
    if chk is None:
        return None
    if chk.refusal is not None:
        return chk.refusal
    if chk.extension is not None:
        log, cfg, _ = _load(repo, agent)
        return DD.extend(log, cfg, chk, "rule.added")
    out = apply_rule_update(repo, rule, agent=agent, operation="created")
    if out.exit == O.OK:
        out.data[answer.relation] = answer.target
        out.data.update({k: v for k, v in chk.data().items() if k != answer.relation})
    return out


def _points_elsewhere(repo: Path, rule: Rule, answer: RuleDedupAnswer | None) -> bool:
    """Whether ``answer`` points at a record that is not a rule."""
    return (
        answer is not None
        and not answer.problem
        and answer.relation != "new"
        and answer.target != rule.id
        and not _is_rule(repo, answer.target)
    )


def _add_checked_against_others(
    repo: Path, rule: Rule, agent: str, answer: RuleDedupAnswer | None
) -> O.Outcome:
    """File ``rule`` -- no other rule reads like it -- unless it reads like a record of
    another kind and nobody has answered ``new``."""

    chk = _cross_kind(repo, rule, agent, DD.Answer("new") if answer else None)
    if chk is not None and chk.refusal is not None:
        return chk.refusal
    out = apply_rule_update(repo, rule, agent=agent, operation="created")
    if chk is not None and out.exit == O.OK:
        out.data.update(chk.data())
    return out


def _compared(rule: Rule, title: str, content: str) -> tuple[float, str, str]:
    """(score, my text, its text) for a new rule against `rule`.

    Content against content, as always, while both have content. A rule with no content
    is a title-only rule, and content alone scored every pair of them 1.0 (two empty
    contents are "identical"): a second title-only rule was refused as a duplicate of each
    of them whatever it said (bug Bd4c9bcb87e). So two title-only rules compare their
    titles, and a title-only rule against one with content compares title and content
    together. With no `title` given, the content-only comparison is kept unchanged."""
    if not title or (content and rule.content):
        return rule.similarity_score(content), content, rule.content
    if not content and not rule.content:
        mine, theirs = title, rule.title
    else:
        mine = "\n".join(x for x in (title, content) if x)
        theirs = "\n".join(x for x in (rule.title, rule.content) if x)
    return dataclasses.replace(rule, content=theirs).similarity_score(mine), mine, theirs


def rule_dedup_check(
    content: str,
    existing_rules: list[Rule],
    threshold: float = 0.55,
    *,
    title: str = "",
) -> tuple[bool, list[dict[str, Any]]]:
    """Check if a rule is similar to existing rules (`_compared` says by what).

    Uses the same similarity engine as the Rule class.

    Args:
        content: The rule content to check
        existing_rules: List of existing Rule objects
        threshold: Score threshold for considering a match (default 0.55)
        title: The rule's title, compared when a rule has no content

    Returns:
        Tuple of (is_duplicate, candidates) where:
        - is_duplicate: True if any score >= threshold
        - candidates: List of similar rules with scores and details,
          sorted by score descending
    """
    candidates: list[dict[str, Any]] = []

    for rule in existing_rules:
        score, text, theirs = _compared(rule, title, content)
        if score >= threshold:
            candidates.append(
                {
                    "id": rule.id,
                    "title": rule.title,
                    "score": score,
                    "scope": rule.scope,
                    "tags": rule.tags,
                    "overlap": _get_overlap_terms(text, theirs),
                }
            )

    # Sort by score descending
    candidates.sort(key=lambda c: -c["score"])
    is_duplicate = len(candidates) > 0
    return is_duplicate, candidates


def rule_dedup_check_dry_run(
    repo: Path,
    content: str,
    threshold: float = 0.55,
    *,
    title: str = "",
    rule_id: str = "",
    agent: str = "",
) -> O.Outcome:
    """Dry-run check: show what dedup would do without writing anything.

    Args:
        repo: Path to the repository root
        content: The rule content to check
        threshold: Score threshold for considering a match

    Returns:
        Outcome showing candidates and whether the add would be refused
    """
    storage = RulesStorage(repo)
    existing_rules = storage.list()

    is_duplicate, candidates = rule_dedup_check(content, existing_rules, threshold, title=title)
    # And every other kind (D-rule-dedupe-everywhere), as `rule add` would meet it.

    probe = Rule(id=rule_id or "rule-check", title=title, content=content)
    chk = _cross_kind(repo, probe, agent, DD.Answer(check_only=True), event_kind="rule.check")
    other = chk.refusal.data if chk is not None and chk.refusal is not None else {}
    if other.get("dedupe_unavailable"):
        return O.failed(
            "rule.check",
            f"the duplicate check could not run: {other['dedupe_unavailable']}",
            candidates=candidates,
        )
    candidates = [*({**c, "kind": "rule"} for c in candidates), *other.get("candidates", [])]
    would_ask = is_duplicate or bool(other.get("would_ask"))

    if not candidates:
        return O.nothing(
            "rule.check",
            "No similar rules or other records found",
            candidates=[],
            would_ask=False,
        )

    return O.ok(
        "rule.check",
        candidates=candidates,
        would_ask=would_ask,
    )


def _get_overlap_terms(content1: str, content2: str, max_terms: int = 5) -> list[str]:
    """Extract the most significant overlapping terms between two contents.

    Uses the tokenization from Rule._tokenize for consistency.

    Args:
        content1: First content string
        content2: Second content string
        max_terms: Maximum number of terms to return

    Returns:
        List of overlapping terms
    """
    from ..services.rules import _tokenize

    tokens1 = set(_tokenize(content1))
    tokens2 = set(_tokenize(content2))
    overlap = sorted(tokens1 & tokens2)
    return overlap[:max_terms]


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
                if rule.priority != DEFAULT_RULE_PRIORITY:
                    lines.append(f"  - Priority: {rule.priority}")

            lines.append("")

    return "\n".join(lines)


def _extend_rule(repo: Path, rule_id: str, new_content: str, agent: str = "") -> O.Outcome:
    """Extend an existing rule by appending new content to it.

    Args:
        repo: Path to the repository root
        rule_id: The rule ID to extend
        new_content: The new content to append
        agent: Agent ID for event logging

    Returns:
        Outcome indicating the rule was extended
    """
    storage = RulesStorage(repo)

    try:
        rule = storage.get(rule_id)
        # Append new content to existing content with separator
        extended_content = f"{rule.content}\n\n---\n\n{new_content}"
        rule, _event_fields = storage.update(rule_id, content=extended_content)

        # Update the manifest
        manifest_content = rules_manifest(storage)
        manifest_path = repo / "DDFLOW.md"
        manifest_path.write_text(manifest_content)

        return O.ok(
            "rule.added",
            id=rule_id,
            extended=rule_id,
        )

    except FileNotFoundError:
        return O.failed("rule.added", f"Rule {rule_id} not found", id=rule_id)
    except Exception as exc:
        return O.failed("rule.added", f"Failed to extend rule: {exc}", id=rule_id)


def apply_rule_update(
    repo: Path, rule: Rule, *, agent: str = "", operation: str = "created"
) -> O.Outcome:
    """Add or update a rule and write the manifest.

    Stores rules in the filesystem and updates the manifest.
    Rules are not logged to the event log (they're filesystem-backed configuration).

    Args:
        repo: Path to the repository root
        rule: The Rule to add or update
        agent: Agent ID for event logging (not currently used for rules)
        operation: Operation kind: "created", "updated"

    Returns:
        Outcome with rule_id and other details
    """
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
            rule, _event_fields = storage.update(
                rule.id,
                **{
                    "title": rule.title,
                    "content": rule.content,
                    "tags": rule.tags,
                    "scope": rule.scope,
                    "priority": rule.priority,
                    "globs": rule.globs,
                },
            )
        else:
            rule, _event_fields = storage.add(rule)

        # Update the manifest
        manifest_content = rules_manifest(storage)
        manifest_path = repo / "DDFLOW.md"
        manifest_path.write_text(manifest_content)

        return O.ok(
            f"rule.{operation}",
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


def _over_limits(repo: Path, rule: Rule, agent: str) -> O.Outcome | None:
    """Refuse a new rule that breaks the project's [rules] limits."""
    cfg = _load(repo, agent)[1]
    problem = ""
    if len(rule.content.encode("utf-8")) > cfg.rules.max_size_bytes:
        problem = f"content is over rules.max_size_bytes ({cfg.rules.max_size_bytes})"
    elif rule.scope not in cfg.rules.scopes_allowed:
        problem = f"scope {rule.scope!r} is not in rules.scopes_allowed {cfg.rules.scopes_allowed}"
    elif cfg.rules.tags_allowed and (
        bad := [t for t in rule.tags if t not in cfg.rules.tags_allowed]
    ):
        problem = f"tags {bad} are not in rules.tags_allowed {cfg.rules.tags_allowed}"
    elif len(RulesStorage(repo).list()) >= cfg.rules.max_rules:
        problem = f"the project already has rules.max_rules ({cfg.rules.max_rules}) rules"
    return O.refused("rule.added", problem) if problem else None


def _refused_up_front(
    repo: Path, rule: Rule, agent: str, answer: RuleDedupAnswer | None
) -> O.Outcome | None:
    """The project's [rules] limits, then an answer that is not one: refused before any
    check runs. Never joined with `or`: an Outcome is falsy unless OK, so a refusal would
    read as "nothing" (roborev)."""
    refusal = _over_limits(repo, rule, agent)
    if refusal is None and answer is not None and answer.problem:
        refusal = O.failed("rule.added", answer.problem, id=rule.id)
    return refusal


def rule_add(
    repo: Path,
    rule: Rule,
    *,
    agent: str = "",
    check_dedup: bool = True,
    dedup_answer: RuleDedupAnswer | None = None,
    dedup_threshold: float = 0.55,
) -> O.Outcome:
    """Add a new rule to the project with dedup checking.

    Args:
        repo: Path to the repository root
        rule: The Rule to add
        agent: Agent ID for event logging
        check_dedup: Whether to check for duplicates (default True)
        dedup_answer: Answer to any dedup candidates ("new", "extends ID", "duplicate_of ID",
            "related ID")
        dedup_threshold: Score threshold for considering a match (default 0.55)

    Returns:
        Outcome with rule_id and other details, or refusal if duplicate found
    """
    refusal = _refused_up_front(repo, rule, agent, dedup_answer)
    if refusal is not None:
        return refusal
    if not check_dedup:
        return apply_rule_update(repo, rule, agent=agent, operation="created")

    # An answer naming a record that is not a rule answers the check against the other
    # kinds (D-rule-dedupe-everywhere).
    if _points_elsewhere(repo, rule, dedup_answer):
        other = _answer_other_kind(repo, rule, dedup_answer, agent)
        if other is not None:
            return other

    # Check for duplicates
    storage = RulesStorage(repo)
    existing_rules = storage.list()

    is_duplicate, candidates = rule_dedup_check(
        rule.content, existing_rules, dedup_threshold, title=rule.title
    )

    # Against every other kind too: a rule restating a decision is refused like a
    # decision restating one. `new` answers both checks.
    if not is_duplicate and (dedup_answer is None or dedup_answer.relation == "new"):
        return _add_checked_against_others(repo, rule, agent, dedup_answer)

    # If no duplicates found, proceed with add
    if not is_duplicate:
        return apply_rule_update(repo, rule, agent=agent, operation="created")

    # Duplicates found - handle based on answer
    if dedup_answer is None:
        # No answer provided - refuse and list candidates
        return O.refused(
            "rule.added",
            "Possible duplicate rule. It reads like:\n"
            + "\n".join(
                f"  {c['id']} ({c['scope']}, score {c['score']:.2f}): "
                f"{c['title']}" + (f" [{', '.join(c['overlap'])}]" if c["overlap"] else "")
                for c in candidates
            )
            + f"\n\nAnswer: new (different rule), extends {candidates[0]['id']} "
            f"(add to existing), duplicate_of {candidates[0]['id']} (same rule), or "
            f"related {candidates[0]['id']} (a different rule about the same thing)",
            id=rule.id,
            candidates=candidates,
        )

    # Validate the answer
    bad = dedup_answer.problem
    if bad:
        return O.failed("rule.added", bad, id=rule.id)

    if dedup_answer.target and dedup_answer.target == rule.id:
        return O.failed("rule.added", "a rule cannot point at itself", id=rule.id)

    # If answer is "new", add anyway
    if dedup_answer.relation == "new":
        return apply_rule_update(repo, rule, agent=agent, operation="created")

    try:
        storage.get(dedup_answer.target)  # must exist; the value is not needed here
    except FileNotFoundError:
        return O.failed(
            "rule.added",
            f"Target rule {dedup_answer.target} not found",
            id=rule.id,
        )

    # "related": a different rule, filed as such. Rules are files, not log records, so
    # there is no link to record; the answer is reported (bug B3be768717c: it was
    # advertised by both surfaces and failed as an unknown relation).
    if dedup_answer.relation == "related":
        out = apply_rule_update(repo, rule, agent=agent, operation="created")
        if out.exit == O.OK:
            out.data["related"] = dedup_answer.target
        return out

    # "extends" or "duplicate_of": always extend the existing rule (merge new content)
    if dedup_answer.relation in ("extends", "duplicate_of"):
        return _extend_rule(repo, dedup_answer.target, rule.content, agent=agent)

    return O.failed("rule.added", f"Unknown relation: {dedup_answer.relation}", id=rule.id)


def _check_edit(
    repo: Path,
    rule_id: str,
    fields: dict[str, Any],
    answer: RuleDedupAnswer | None,
    agent: str,
) -> tuple[O.Outcome | None, dict[str, Any]]:
    """(why the edit stops -- None when it goes ahead --, what its result carries about
    the check: `related`, `candidates`, `dedupe_unavailable`). Only a new title or content
    is checked; an answer naming another RULE relates the two and needs no check against
    the other kinds, as on `rule add`."""
    if "title" not in fields and "content" not in fields:
        return None, {}
    storage = RulesStorage(repo)
    try:
        current = storage.get(rule_id)
    except FileNotFoundError:
        return O.failed("rule.updated", f"Rule {rule_id} not found", id=rule_id), {}
    if answer is not None and answer.problem:
        return O.failed("rule.updated", answer.problem, id=rule_id), {}
    if answer is not None and answer.target == rule_id:
        return O.failed("rule.updated", "a rule cannot point at itself", id=rule_id), {}
    if answer is not None and answer.relation in ("extends", "duplicate_of"):
        return O.failed(
            "rule.updated",
            f"an edit cannot be folded into another record: answer new or related "
            f"{answer.target}, or `ddflow rule remove {rule_id}` and extend {answer.target}",
            id=rule_id,
        ), {}
    related = {"related": answer.target} if answer and answer.relation == "related" else {}
    if answer is not None and answer.target and _is_rule(repo, answer.target):
        return None, related
    edited = dataclasses.replace(
        current,
        title=fields.get("title", current.title),
        content=fields.get("content", current.content),
    )
    chk = _cross_kind(
        repo,
        edited,
        agent,
        DD.Answer(answer.relation, answer.target) if answer else None,
        event_kind="rule.updated",
    )
    if chk is None:
        return None, related
    if chk.refusal is not None:
        return chk.refusal, {}
    return None, {**chk.data(), **related}


def rule_update(
    repo: Path,
    rule_id: str,
    *,
    dedup_answer: RuleDedupAnswer | None = None,
    agent: str = "",
    **fields: Any,
) -> O.Outcome:
    """Update an existing rule.

    A new title or content is checked against every other record kind first, as an add
    is (D-rule-dedupe-everywhere): refused while it reads like a decision, lesson, task...
    until answered ``new`` or ``related ID``. An edit cannot be folded INTO another
    record (``extends`` / ``duplicate_of``): remove the rule and extend that record.

    Args:
        repo: Path to the repository root
        rule_id: The rule ID to update
        dedup_answer: The answer to the duplicate check (new | related ID)
        agent: Agent ID for the duplicate check
        **fields: Fields to update

    Returns:
        Outcome indicating success or failure
    """
    storage = RulesStorage(repo)
    stop, extra = _check_edit(repo, rule_id, fields, dedup_answer, agent)
    if stop is not None:
        return stop

    try:
        _rule, _event_fields = storage.update(rule_id, **fields)

        # Update the manifest
        manifest_content = rules_manifest(storage)
        manifest_path = repo / "DDFLOW.md"
        manifest_path.write_text(manifest_content)

        return O.ok(
            "rule.updated",
            id=rule_id,
            **fields,
            **extra,
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
    storage = RulesStorage(repo)

    try:
        storage.remove(rule_id)

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
    import re

    storage = RulesStorage(repo)

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
            elif regex:
                # Regex match
                try:
                    if re.search(query, combined, re.IGNORECASE):
                        score = 0.8
                except re.error:
                    continue
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

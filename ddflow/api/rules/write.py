"""Write a rule file, its manifest and its event-log record.

`rules_manifest` renders the DDFLOW.md view; `apply_rule_update` / `_extend_rule` write the
file and the manifest, then `_record_in_log` / `_retire_in_log` make the log say what the
file now says (D-unify 7).
"""

from __future__ import annotations

from pathlib import Path

from ...core import defs as D
from ...core import outcome as O
from ...infra.fsio import replace_text
from ...services.guidance import deffields as DF
from ...services.guidance.kinds import RULE
from ...services.guidance.limits import lint, over_limit
from ...services.guidance.store import GuidanceFiles
from ...services.rules import Rule, RulesStorage
from .._base import _load
from ..defs import def_record_unchecked, def_retire, def_update
from .dedup import RuleDedupAnswer

DEFAULT_RULE_PRIORITY = 50


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


def _record_in_log(repo: Path, rule_id: str, agent: str, outcome: O.Outcome) -> O.Outcome:
    """Say in the log what the rule file now says: the log holds the rule (a `rule` definition,
    D-unify 7) and the file is its view. Called after the file is written, so an op that
    fails writes nothing in either place; a record this cannot append (a lock timeout, an
    older ddflow refused by the skew guard) leaves the file for the `rules-to-events`
    migration to record, and the result says so. ``outcome`` is returned when it worked."""
    try:
        rec = RulesStorage(repo).get(rule_id).to_record()
        fields = DF.to_fields(rec, RULE)
        prov = {"via": "write", **DF.to_provenance(rec)}
        files = GuidanceFiles(repo, RULE)
        source = files.path(rule_id).relative_to(Path(repo)).as_posix()
        log_cfg = _load(repo, agent)
        known = log_cfg[2].defs.get(D.key(RULE.kind, rule_id))
        if known is not None and known.live:
            if D.same(DF.logged(fields, log_cfg[1]), known.digest):
                return outcome  # a re-save that changed no content (a timestamp is not content)
            res = def_update(
                repo, RULE.kind, rule_id, fields, source=source, provenance=prov, agent=agent
            )
        else:  # new, or brought back after a removal
            res = def_record_unchecked(
                repo, RULE.kind, rule_id, fields, source=source, provenance=prov, agent=agent
            )
    except Exception as exc:
        return _unrecorded(outcome, str(exc))
    return outcome if res.exit in (O.OK, O.NOTHING) else _unrecorded(outcome, res.reason)


def _retire_in_log(repo: Path, rule_id: str, agent: str, outcome: O.Outcome) -> O.Outcome:
    """A removed rule file retires its definition (`def.retired`; the history stays). A rule
    the log never recorded has nothing to retire."""
    try:
        known = _load(repo, agent)[2].defs.get(D.key(RULE.kind, rule_id))
        if known is None or not known.live:
            return outcome
        res = def_retire(repo, RULE.kind, rule_id, reason="the rule was removed", agent=agent)
    except Exception as exc:
        return _unrecorded(outcome, str(exc), removed=True)
    if res.exit in (O.OK, O.NOTHING):
        return outcome
    return _unrecorded(outcome, res.reason, removed=True)


def _unrecorded(outcome: O.Outcome, why: str, *, removed: bool = False) -> O.Outcome:
    """``outcome`` turned into a failure that says the rule file was written (or ``removed``)
    and the log could not say so. The file is as the caller asked; the log is not."""
    if removed:
        note = (
            f"the rule file was removed but the log still holds the rule ({why}); "
            f"add it again and remove it to retire it"
        )
    else:
        note = (
            f"the rule file was written but not recorded in the log ({why}); "
            f"`ddflow upgrade` records it"
        )
    # A FAILURE that says what is on disk: every surface shows a failure's reason, and a
    # payload field would have been invisible to the ones that project the result.
    return O.failed(outcome.kind, note, **outcome.data)


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
        replace_text(manifest_path, manifest_content)

        return _record_in_log(
            repo,
            rule_id,
            agent,
            O.ok(
                "rule.added",
                id=rule_id,
                extended=rule_id,
            ),
        )

    except FileNotFoundError:
        return O.failed("rule.added", f"Rule {rule_id} not found", id=rule_id)
    except Exception as exc:
        return O.failed("rule.added", f"Failed to extend rule: {exc}", id=rule_id)


def apply_rule_update(
    repo: Path, rule: Rule, *, agent: str = "", operation: str = "created"
) -> O.Outcome:
    """Add or update a rule and write the manifest.

    Writes the rule file and the manifest, then records the rule's definition in the log
    (`_record_in_log`): the file is the view of that record (D-unify 7).

    Args:
        repo: Path to the repository root
        rule: The Rule to add or update
        agent: Agent ID the definition's provenance names
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
        replace_text(manifest_path, manifest_content)

        return _record_in_log(
            repo,
            rule.id,
            agent,
            O.ok(
                f"rule.{operation}",
                id=rule.id,
                title=rule.title,
                scope=rule.scope,
            ),
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
    problem = over_limit(rule.to_record(), RULE.limits(cfg), lambda: len(RulesStorage(repo).list()))
    return O.refused("rule.added", problem) if problem else None


def _refused_up_front(
    repo: Path, rule: Rule, agent: str, answer: RuleDedupAnswer | None
) -> O.Outcome | None:
    """The project's [rules] limits, then an answer that is not one: refused before any
    check runs. Never joined with `or`: an Outcome is falsy unless OK, so a refusal would
    read as "nothing" (roborev)."""
    refusal = _over_limits(repo, rule, agent)
    if refusal is None and (problems := lint(rule.to_record(), RULE)):
        # A file whose id no loader accepts would be written and then never found again.
        refusal = O.failed("rule.added", problems[0], id=rule.id)
    if refusal is None and answer is not None and answer.problem:
        refusal = O.failed("rule.added", answer.problem, id=rule.id)
    return refusal

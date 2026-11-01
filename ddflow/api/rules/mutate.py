"""`rule update` and `rule remove`: change or delete a rule file and its record."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import ddflow.api._dedupe as DD

from ...core import outcome as O
from ...infra.fsio import replace_text
from ...services.guidance.record import ENFORCEMENTS
from ...services.rules import RulesStorage
from .dedup import RuleDedupAnswer, _cross_kind, _is_rule
from .write import _record_in_log, _retire_in_log, rules_manifest


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
        # A relation to another rule answers nothing about the other kinds: they are
        # checked as if unanswered (rubber-duck, critic).
        answer = None
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
    enforcement = fields.get("enforcement")
    if enforcement is not None and enforcement not in ENFORCEMENTS:
        # Refused BEFORE the write: the file would otherwise be saved with a value no
        # later read accepts.
        return O.failed(
            "rule.updated",
            f"unknown enforcement {enforcement!r}: one of {', '.join(ENFORCEMENTS)}",
            id=rule_id,
        )
    storage = RulesStorage(repo)
    stop, extra = _check_edit(repo, rule_id, fields, dedup_answer, agent)
    if stop is not None:
        return stop

    try:
        _rule, _event_fields = storage.update(rule_id, **fields)

        # Update the manifest
        manifest_content = rules_manifest(storage)
        manifest_path = repo / "DDFLOW.md"
        replace_text(manifest_path, manifest_content)

        return _record_in_log(
            repo,
            rule_id,
            agent,
            O.ok(
                "rule.updated",
                id=rule_id,
                **fields,
                **extra,
            ),
        )

    except FileNotFoundError:
        return O.failed("rule.updated", f"Rule {rule_id} not found", id=rule_id)
    except Exception as exc:
        return O.failed("rule.updated", f"Failed to update rule: {exc}", id=rule_id)


def rule_remove(repo: Path, rule_id: str, *, agent: str = "") -> O.Outcome:
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
        replace_text(manifest_path, manifest_content)

        return _retire_in_log(repo, rule_id, agent, O.ok("rule.deleted", id=rule_id))

    except FileNotFoundError:
        return O.failed("rule.deleted", f"Rule {rule_id} not found", id=rule_id)
    except Exception as exc:
        return O.failed("rule.deleted", f"Failed to delete rule: {exc}", id=rule_id)

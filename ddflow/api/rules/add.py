"""`rule add`: file a new rule, checked against other rules and every other record kind."""

from __future__ import annotations

from pathlib import Path

import ddflow.api._dedupe as DD

from ...core import outcome as O
from ...services.rules import Rule, RulesStorage
from .._base import _load
from .dedup import (
    RuleDedupAnswer,
    _cross_kind,
    _other_kind_rows,
    _points_elsewhere,
    rule_dedup_check,
)
from .write import _extend_rule, _refused_up_front, apply_rule_update


def _answer_other_kind(
    repo: Path, rule: Rule, answer: RuleDedupAnswer, agent: str
) -> O.Outcome | None:
    """An answer that points at a decision, lesson, task... rather than a rule. None
    when the cross-kind check is off. ``duplicate_of`` an open record, and ``extends`` one
    outside `_dedupe.LINK_ONLY_KINDS`, put the rule's text on that record
    (`record.extended`) and file no rule, as every other add does; ``extends`` a decision or
    task, onto a closed or claimed record, and ``related``, the rule is filed and the
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


def _related_to_rule(
    repo: Path, rule: Rule, agent: str, answer: RuleDedupAnswer | None
) -> O.Outcome:
    """File ``rule`` with an answer that names another RULE: checked against the other
    kinds as if unanswered -- a relation to a rule says nothing about a decision it may
    restate -- and reported as ``related`` when it is one."""
    out = _add_checked_against_others(repo, rule, agent, None)
    if out.exit == O.OK and answer is not None and answer.relation == "related":
        out.data["related"] = answer.target
    return out


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

    # No other rule reads like it, and the answer names a rule: that answers nothing about
    # the other kinds, so they are still checked (rubber-duck, critic).
    if not is_duplicate:
        return _related_to_rule(repo, rule, agent, dedup_answer)

    # Duplicates found - handle based on answer
    if dedup_answer is None:
        # No answer provided - refuse and list candidates: the other rules, and the
        # records of other kinds it reads like too (rubber-duck), so that `new` answers
        # what the adder was actually shown.
        others = _other_kind_rows(repo, rule, agent)
        return O.refused(
            "rule.added",
            "Possible duplicate rule. It reads like:\n"
            + "\n".join(
                f"  {c['id']} ({c['scope']}, score {c['score']:.2f}): "
                f"{c['title']}" + (f" [{', '.join(c['overlap'])}]" if c["overlap"] else "")
                for c in candidates
            )
            + "".join(
                f"\n  {c['id']} ({c['kind']}, {c['state']}, score {c['score']:.2f}): "
                f"{c['title'][:100]}"
                for c in others
            )
            + f"\n\nAnswer: new (different rule), extends {candidates[0]['id']} "
            f"(add to existing), duplicate_of {candidates[0]['id']} (same rule), or "
            f"related {candidates[0]['id']} (a different rule about the same thing)",
            id=rule.id,
            candidates=[*({**c, "kind": "rule"} for c in candidates), *others],
        )

    # The answer itself was validated up front (`_refused_up_front`).

    if dedup_answer.target and dedup_answer.target == rule.id:
        return O.failed("rule.added", "a rule cannot point at itself", id=rule.id)

    # If answer is "new", add anyway -- it answered the other kinds' candidates too
    if dedup_answer.relation == "new":
        return _add_checked_against_others(repo, rule, agent, dedup_answer)

    try:
        storage.get(dedup_answer.target)  # must exist; the value is not needed here
    except FileNotFoundError:
        return O.failed(
            "rule.added",
            f"Target rule {dedup_answer.target} not found",
            id=rule.id,
        )

    # "related": a different rule, filed as such. A rule definition has no link field, so
    # there is no link to record; the answer is reported (bug B3be768717c: it was
    # advertised by both surfaces and failed as an unknown relation).
    if dedup_answer.relation == "related":
        return _related_to_rule(repo, rule, agent, dedup_answer)

    # "extends" or "duplicate_of": always extend the existing rule (merge new content)
    if dedup_answer.relation in ("extends", "duplicate_of"):
        return _extend_rule(repo, dedup_answer.target, rule.content, agent=agent)

    return O.failed("rule.added", f"Unknown relation: {dedup_answer.relation}", id=rule.id)

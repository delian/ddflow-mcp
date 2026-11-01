"""Rule dedup: the similarity check against other rules and against every other record kind."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import ddflow.api._dedupe as DD

from ...core import outcome as O
from ...services.guidance.similarity import similar
from ...services.rules import Rule, RulesStorage
from .._base import _load


@dataclass(frozen=True)
class RuleDedupAnswer:
    """Response to a rule dedup check: new, extends, or duplicate_of."""

    relation: str = ""  # "new", "extends", "duplicate_of"
    target: str = ""  # The rule ID to point at

    @classmethod
    def parse(cls, spec: str) -> RuleDedupAnswer:
        """Parse 'new', 'extends ID', 'duplicate_of ID', or 'duplicate ID'."""
        return cls(*DD.split_answer(spec))

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


def _other_kind_rows(repo: Path, rule: Rule, agent: str) -> list[dict[str, Any]]:
    """The records of other kinds ``rule`` reads like, as the check would list them;
    nothing written. Empty while the cross-kind check is off or cannot run."""
    chk = _cross_kind(repo, rule, agent, DD.Answer(check_only=True))
    if chk is None or chk.refusal is None:
        return []
    return list(chk.refusal.data.get("candidates", []))


def _points_elsewhere(repo: Path, rule: Rule, answer: RuleDedupAnswer | None) -> bool:
    """Whether ``answer`` points at a record that is not a rule."""
    return (
        answer is not None
        and not answer.problem
        and answer.relation != "new"
        and answer.target != rule.id
        and not _is_rule(repo, answer.target)
    )


def rule_dedup_check(
    content: str,
    existing_rules: list[Rule],
    threshold: float = 0.55,
    *,
    title: str = "",
) -> tuple[bool, list[dict[str, Any]]]:
    """Check if a rule is similar to existing rules (`guidance.similarity.compared` says by what).

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
    found = similar(
        [r.to_record() for r in existing_rules], content, title=title, threshold=threshold
    )
    candidates: list[dict[str, Any]] = [
        {
            "id": f.record.id,
            "title": f.record.title,
            "score": f.score,
            "scope": f.record.level,
            "tags": f.record.tags,
            "overlap": f.overlap,
        }
        for f in found
    ]
    return bool(candidates), candidates


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

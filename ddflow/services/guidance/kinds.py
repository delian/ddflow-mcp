"""The two kinds of guidance, and the adapters from their existing records.

`rule` (a file under .ddflow/rules/) and `decision` (an event in the log) keep their own
types and names; this is where each says what it adds to the shared `GuidanceRecord`.
"""

from __future__ import annotations

import re

from ...core.model import State
from ...core.records import Decision
from .record import ACCEPTED, GuidanceRecord, KindSpec, Scope
from .resolve import ALWAYS, GLOBS, resolve

_RULE_ID = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")


def is_valid_rule_id(rule_id: str) -> bool:
    """A rule id is kebab-case with an ``r-`` prefix: ``r-naming``, ``r-test-coverage``."""
    return rule_id.startswith("r-") and bool(_RULE_ID.fullmatch(rule_id[2:]))


RULE = KindSpec(
    kind="rule",
    label="Rule",
    directory="rules",
    section="rules",
    default_level="project",
    stamped=True,
    valid_id=is_valid_rule_id,
    id_hint="must be kebab-case with 'r-' prefix (e.g., 'r-naming')",
)

DECISION = KindSpec(
    kind="decision",
    label="Decision",
    directory="decisions",
    # D-decision-management: a decision warns by default; it may be set to block or advisory.
    default_enforcement="warn",
    extras=(
        ("context", ""),
        ("consequences", ""),
        ("alternatives", ""),
        ("decided_by", ""),
        ("item", ""),
        ("supersedes", []),
        ("superseded_by", ""),
    ),
    id_hint="must not be empty",
)

KINDS: dict[str, KindSpec] = {RULE.kind: RULE, DECISION.kind: DECISION}


def decision_record(d: Decision) -> GuidanceRecord:
    """A logged decision as guidance: its ``decision`` text is the body, its globs the scope.

    ``status`` is the decision's own (``accepted`` unless recorded otherwise); a superseded
    decision stays ``accepted`` here with ``superseded_by`` set, and `live` reads both, as
    `Decision.live` does."""
    return GuidanceRecord(
        id=d.id,
        kind=DECISION.kind,
        title=d.title,
        body=d.decision,
        scope=Scope(globs=tuple(d.globs)),
        tags=list(d.tags),
        enforcement=DECISION.default_enforcement,
        status=d.status or ACCEPTED,
        sources=list(d.sources),
        provenance={"at": d.at, "by": d.by},
        ext={
            "context": d.context,
            "consequences": d.consequences,
            "alternatives": d.alternatives,
            "decided_by": d.decided_by,
            "item": d.item,
            "supersedes": list(d.supersedes),
            "superseded_by": d.superseded_by,
        },
    )


def governing(state: State, globs: list[str]) -> tuple[list[Decision], list[Decision]]:
    """The live decisions that govern work on ``globs``: (those whose own globs overlap
    them, those with no globs and so project-wide), each in the order the log recorded them.

    What `decision applicable` and `brief` hand an agent about to write those files."""
    applied = resolve((decision_record(d) for d in state.decisions.values()), globs=globs)
    by_id = state.decisions
    return (
        [by_id[a.record.id] for a in applied if a.reason == GLOBS],
        [by_id[a.record.id] for a in applied if a.reason == ALWAYS],
    )

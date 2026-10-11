"""The two kinds of guidance, and the adapters from their existing records.

`rule` (a file under .ddflow/rules/) and `decision` (an event in the log) keep their own
types and names; this is where each says what it adds to the shared `GuidanceRecord`.
"""

from __future__ import annotations

import re

from ...config import Config
from ...core.model import State
from ...core.records import Decision
from ...core.schedule import conflicts
from .record import ACCEPTED, GuidanceRecord, KindSpec, Limits, Scope
from .resolve import ALWAYS, GLOBS, resolve

_RULE_ID = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")


def is_valid_rule_id(rule_id: str) -> bool:
    """A rule id is kebab-case with an ``r-`` prefix: ``r-naming``, ``r-test-coverage``."""
    return rule_id.startswith("r-") and bool(_RULE_ID.fullmatch(rule_id[2:]))


def rule_limits(cfg: Config) -> Limits:
    """The ``[rules]`` caps."""
    r = cfg.rules
    return Limits("rules", r.max_rules, r.max_size_bytes, r.scopes_allowed, r.tags_allowed)


RULE = KindSpec(
    kind="rule",
    label="Rule",
    directory="rules",
    limits=rule_limits,
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
            "contested": bool(d.contested),
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


def overlapping_live(state: State, globs: list[str], *, exclude: str = "") -> list[Decision]:
    """The live decisions with their own globs that overlap ``globs`` -- the matcher
    `governing` uses -- in log order. What `decision add` records as `saw`."""
    return [
        d
        for d in state.decisions.values()
        if d.live and d.id != exclude and d.globs and conflicts(globs, d.globs)
    ]


def unreconciled(state: State) -> list[tuple[Decision, Decision]]:
    """Pairs of live decisions on overlapping globs that were recorded without seeing each
    other (B0dcccd8594), each pair once, ordered by id.

    A decision that names the other in its `saw`, or that supersedes it (then the other is
    not live), is reconciled. A decision with no `saw` key (written before it existed) never
    forms a pair, so an old log folds as it did."""
    live = sorted(
        (d for d in state.decisions.values() if d.live and d.globs and d.saw is not None),
        key=lambda d: d.id,
    )
    return [
        (a, b)
        for i, a in enumerate(live)
        for b in live[i + 1 :]
        if a.id not in (b.saw or ()) and b.id not in (a.saw or ()) and conflicts(a.globs, b.globs)
    ]


def unreconciled_with(state: State) -> dict[str, list[str]]:
    """`unreconciled`, as {decision id: the ids it is unreconciled with}."""
    out: dict[str, list[str]] = {}
    for a, b in unreconciled(state):
        out.setdefault(a.id, []).append(b.id)
        out.setdefault(b.id, []).append(a.id)
    return {k: sorted(v) for k, v in out.items()}

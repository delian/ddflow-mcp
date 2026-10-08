"""One applicability resolver: which guidance governs this work, and why.

The question was answered three times with three meanings: `Rule.matches_globs` (a toy
pattern-equality test no caller reached), `api.decisions.decision_applicable` and
`api.lifecycle.brief`'s own filter (both: a live decision whose globs overlap the item's, or
one with no globs). This is the one answer; a rule and a decision go through it alike.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ...core.schedule import conflicts
from .record import GuidanceRecord

ALWAYS = "always"
GLOBS = "globs"
CATEGORY = "category"
GATE = "gate"


@dataclass(frozen=True)
class Applies:
    """A piece of guidance that governs the work, and the reason it does."""

    record: GuidanceRecord
    #: ``always`` (no restriction), or the most specific restriction it met: ``globs``,
    #: ``category`` or ``gate``, in that order of precedence when it has several.
    reason: str
    #: For ``globs``: the (item glob, guidance glob) pairs that overlap.
    matched: tuple[tuple[str, str], ...] = ()


def _applies(
    rec: GuidanceRecord,
    globs: list[str],
    gate: str,
    categories: frozenset[str],
    shared: list[str] | None,
) -> Applies | None:
    scope = rec.scope
    if scope.always:
        return Applies(rec, ALWAYS)
    reason = ""
    pairs: tuple[tuple[str, str], ...] = ()
    if scope.gates:
        if gate not in scope.gates:
            return None
        reason = GATE
    if scope.categories:
        if not categories.intersection(scope.categories):
            return None
        reason = CATEGORY
    if scope.globs:
        hit = conflicts(globs, list(scope.globs), shared)
        if not hit:
            return None
        reason, pairs = GLOBS, tuple(hit)
    return Applies(rec, reason, pairs)


def resolve(
    records: Iterable[GuidanceRecord],
    *,
    globs: Iterable[str] = (),
    gate: str = "",
    categories: Iterable[str] = (),
    shared: list[str] | None = None,
) -> list[Applies]:
    """The live guidance among ``records`` that governs work on ``globs`` (the item's
    declared files), at ``gate``, in ``categories``, highest priority first and otherwise in
    the order given.

    A dimension a record sets must hold; one it leaves unset restricts nothing. Guidance
    with no restriction at all applies always. ``shared`` are globs that overlap nothing
    (`[lease] shared_globs`)."""
    mine = list(globs)
    cats = frozenset(categories)
    found = [a for r in records if r.live and (a := _applies(r, mine, gate, cats, shared))]
    return sorted(found, key=lambda a: -a.record.priority)

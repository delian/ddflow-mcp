"""Every descriptor answers every catalog row (PAR-answers).

A catalog row asks one question of the descriptor, and the descriptor answers it in the facts
three-valued states (``services.facts``: ``verified``, ``documented``, ``unverified``,
``none``): the row's named fields are VERIFIED (the overlay checked it), DOCUMENTED (the
descriptor declares them), left out (``unverified``), ``none = true`` is a DOCUMENTED ABSENCE,
and a row with no descriptor field is NOT APPLICABLE (a documented ddflow-side knob). The
answer is DERIVED from ``harnessfacts`` and the descriptor, one grader, never guessed.
"""

from __future__ import annotations

from .. import facts, harnessfacts
from .. import harnessreg as HR
from . import catalog

#: weakest-link ranks for the answer: verified > documented > unverified > none, so a row is
#: only as strong as its weakest named field.
_RANK = {"verified": 3, "documented": 2, "unverified": 1, "none": 0}


def _field_status(h: HR.Harness, field: str) -> str:
    """The facts status for one catalog ``descriptor_field``: harnessfacts' status when it
    lists the field, else the owning section's presence (declared => documented, absent =>
    unverified, ``mcp.shape = "none"`` => none)."""
    rows = {r["fact"]: r["status"] for r in harnessfacts.rows(h, absent=True)}
    if field in rows:
        return rows[field]
    name = field.partition(".")[0]
    section = getattr(h, name, None)
    if section is None:
        return "unverified"
    if name == "mcp" and getattr(section, "shape", "") == "none":
        return "none"
    return "documented"


def answer(h: HR.Harness, row) -> str:
    """The answer status for ``h`` on ``row``: the weakest-link over its named fields'
    ``facts`` statuses, or ``none`` for a row with no descriptor field (a documented gap)."""
    if not row.descriptor_fields:
        return "none"
    statuses = {_field_status(h, field) for field in row.descriptor_fields}
    return min(statuses, key=_RANK.get)


def answers(h: HR.Harness) -> dict[str, str]:
    """``{catalog-row feature: status}`` for every active catalog row."""
    return {row.feature: answer(h, row) for row in catalog.load().rows}


def unanswered(h: HR.Harness) -> list[str]:
    """Catalog rows ``h`` has no answer for: the derivation is total (a row is graded or a
    documented gap, both answered), so this is empty and non-emptiness is the lint failure."""
    return sorted(feature for feature, status in answers(h).items() if status not in facts.STATUSES)

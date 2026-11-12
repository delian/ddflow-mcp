"""Every descriptor answers every catalog row (PAR-answers).

A catalog row asks one question of the descriptor, and the descriptor answers it in the facts
three-valued states (``services.facts``: ``verified``, ``documented``, ``unverified``,
``none``): the row is SUPPORTED (its named section is declared), a DOCUMENTED ABSENCE
(``none = true``), left out (``unverified``), or NOT APPLICABLE (a documented ddflow-side
knob -- ``descriptor_fields`` empty plus a note, carried by the loader as a documented gap).
The answer is DERIVED from what the descriptor already declares, one grader, never guessed.
"""

from __future__ import annotations

from .. import facts
from .. import harnessreg as HR
from . import catalog


def answer(h: HR.Harness, row) -> str:
    """The status in ``facts.STATUSES`` for ``h`` on ``row``: SUPPORTED (verified) when every
    named section is declared, a DOCUMENTED ABSENCE (none) when one says ``none = true``, left
    out (unverified) otherwise -- or ``none`` for a row with no descriptor field (a documented
    gap)."""
    if not row.descriptor_fields:
        return "none"
    statuses = set()
    for name in sorted({f.partition(".")[0] for f in row.descriptor_fields}):
        record = getattr(h, name, None)
        if record is None:
            statuses.add("unverified")
        elif getattr(record, "none", False):
            statuses.add("none")
        else:
            statuses.add("verified")
    if "none" in statuses:
        return "none"
    if "unverified" in statuses:
        return "unverified"
    return "verified"


def answers(h: HR.Harness) -> dict[str, str]:
    """``{catalog-row feature: status}`` for every active catalog row."""
    return {row.feature: answer(h, row) for row in catalog.load().rows}


def unanswered(h: HR.Harness) -> list[str]:
    """Catalog rows ``h`` has no answer for. The derivation is total (a row is graded or a
    documented gap, both answered), so this is empty and non-emptiness is the lint failure."""
    return sorted(feature for feature, status in answers(h).items() if status not in facts.STATUSES)

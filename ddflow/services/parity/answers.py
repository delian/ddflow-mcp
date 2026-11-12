"""Every descriptor answers every catalog row (PAR-answers).

A catalog row asks one question of the descriptor, and the descriptor answers it in the facts
three-valued states (``services.facts``: ``verified``, ``documented``, ``unverified``,
``none``): the row is SUPPORTED (its named section is declared), a DOCUMENTED ABSENCE
(``none = true``), left out (``unverified``), or NOT APPLICABLE (a ddflow-side knob with no
descriptor field -- the reason is the row's note). The answer is DERIVED from what the
descriptor already declares, one grader, never guessed.
"""

from __future__ import annotations

from .. import facts
from .. import harnessreg as HR
from . import catalog


def answer(h: HR.Harness, row) -> str:
    """The status in ``facts.STATUSES`` for ``h`` on ``row``: ``facts.best`` over the sections
    its fields name -- declared => verified, ``none = true`` => none, absent => unverified --
    or ``none`` for a row with no descriptor field (a ddflow-side knob)."""
    if not row.descriptor_fields:
        return "none"
    sections = sorted({f.partition(".")[0] for f in row.descriptor_fields})
    have = []
    for name in sections:
        record = getattr(h, name, None)
        if record is None:
            have.append({"s": "unverified"})
        elif getattr(record, "none", False):
            have.append({"s": "none"})
        else:
            have.append({"s": "verified"})
    return facts.best(have)["s"]


def answers(h: HR.Harness) -> dict[str, str]:
    """``{catalog-row feature: status}`` for every active catalog row."""
    return {row.feature: answer(h, row) for row in catalog.load().rows}


def unanswered(h: HR.Harness) -> list[str]:
    """Catalog rows ``h`` has no answer for: by construction none, so the lint's "unanswered
    row" is this list's non-emptiness (the derivation is total)."""
    return sorted(feature for feature, status in answers(h).items() if status not in facts.STATUSES)

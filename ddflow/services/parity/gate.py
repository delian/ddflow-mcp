"""The supported gate (PAR-supported-gate): 'supported' means every required row answered.

Every shipped descriptor is a supported agent, listed at BOTH support tiers -- the two
directions of the parity contract -- and each tier REQUIRES its catalog rows (the rows on
that axis; the required rows are DATA: ``catalog.toml``'s axis sections, never code). A
required row is ANSWERED at ``verified`` / ``documented`` / ``none`` and only LISTED at
``unverified``: an ``unverified`` row is a legal answer (the matrix shows it) but does not
count as supported, so a descriptor may not claim a tier while a required row is still
unverified. The conformance ratchet (``tests/harness_conformance``) pins the unverified set
to shrink-only, so a new catalog row -- answered ``unverified`` by every agent -- fails the
ratchet until every agent answers it.
"""

from __future__ import annotations

from .. import harnessreg as HR
from . import answers as AN
from . import catalog as CAT

#: A support tier is an axis of the catalog; its required rows are its (data) rows.
TIERS = (CAT.IN_AGENT, CAT.ORCHESTRATOR)

#: An answer that ANSWERS a required row; ``unverified`` is listed, not answered.
_ANSWERED = frozenset({"verified", "documented", "none"})


def required_rows(tier: str) -> tuple[CAT.Row, ...]:
    """The catalog rows ``tier`` requires, in catalogue order (data, not code)."""
    return CAT.load().by_axis(tier)


def required_features(tier: str) -> tuple[str, ...]:
    return tuple(r.feature for r in required_rows(tier))


def unanswered(h: HR.Harness, tier: str | None = None) -> tuple[str, ...]:
    """The required features ``h`` answers ``unverified``: listed but not yet answered.

    ``tier is None`` means over every tier."""
    if tier is None:
        return tuple(f for t in TIERS for f in _unanswered(h, t))
    return _unanswered(h, tier)


def _unanswered(h: HR.Harness, tier: str) -> tuple[str, ...]:
    got = AN.answers(h)
    return tuple(r.feature for r in required_rows(tier) if got[r.feature] == "unverified")


def answered(h: HR.Harness, tier: str | None = None) -> tuple[str, ...]:
    """The required features ``h`` has ANSWERED (every status but ``unverified``)."""
    got = AN.answers(h)
    if tier is None:
        tiers = TIERS
    else:
        tiers = (tier,)
    return tuple(f for t in tiers for f in required_features(t) if got.get(f) in _ANSWERED)


def supported(h: HR.Harness, tier: str | None = None) -> bool:
    """Whether ``h`` answers every required row of ``tier`` (no ``unverified``)."""
    return not unanswered(h, tier)


def listed(h: HR.Harness, tier: str | None = None) -> tuple[str, ...]:
    """The required features ``h`` has an answer for (any status) -- the total derivation
    answers every row, so this is the tier's full feature list."""
    if tier is None:
        tiers = TIERS
    else:
        tiers = (tier,)
    return tuple(f for t in tiers for f in required_features(t))


def unlisted(h: HR.Harness, tier: str | None = None) -> tuple[str, ...]:
    """Required features ``h`` has no answer for. The derivation is total (every row is
    graded or a documented gap, both answered), so this is empty."""
    got = set(AN.answers(h))
    if tier is None:
        tiers = TIERS
    else:
        tiers = (tier,)
    return tuple(f for t in tiers for f in required_features(t) if f not in got)


def unsupported(hs: list[HR.Harness] | None = None) -> dict[str, list[str]]:
    """Per agent, the tiers it may NOT claim as supported (a required row is unverified).
    A descriptor with a tier missing from its list answers every required row of it."""
    hs = list(HR.load() if hs is None else hs)
    out: dict[str, list[str]] = {}
    for h in hs:
        open_tiers = [t for t in TIERS if unanswered(h, t)]
        if open_tiers:
            out[h.id] = open_tiers
    return out

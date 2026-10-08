"""Waivers: a finding that is accepted for now, on the record (B-uni-guidance-enforce).

A violation of guidance is fixed, waived or answered with a superseding proposal for the
operator (D-decision-management). A waiver is the middle one, and it is deliberately narrow:

* it names the record, and optionally one check, it covers (``check`` empty: every check);
* it is SCOPED to globs: a finding in another file is not covered;
* it carries a reason, and an expiry of at most `MAX_DAYS` days after it was granted -- an
  open-ended waiver is a decision nobody made;
* it is approved by a person, through the one approve-by-digest primitive
  (`services.approval`): the digest is the waiver's own content, so editing a waiver (widening
  its globs, moving its expiry) is not approved until a person approves it again;
* once it expires it covers nothing and the finding is live again (`resurfaced`).

This module is the pure half: the record, its validation and `apply`. Where waivers are stored
(events and `decision waive`) is P-decisions' (B-dec-waivers), built on this.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any

from ...core import clock
from ...core import globs as G
from ...core.events import canonical_digest
from ..approval import check as approval_check
from .checks import FAIL, WAIVED, CheckResult, Finding

#: The longest a waiver may last (D-decision-management 3).
MAX_DAYS = 90

ACTIVE, EXPIRED, UNAPPROVED = "active", "expired", "unapproved"


@dataclass(frozen=True)
class Waiver:
    id: str
    #: The guidance record it is for.
    record: str
    reason: str
    #: Files it covers; a finding outside them is not waived.
    globs: tuple[str, ...]
    #: The last day it holds (ISO date, inclusive).
    expires: str
    #: The day it was granted (ISO date): the 90 days count from it.
    granted: str
    #: One check of the record; "" covers every check of it.
    check: str = ""

    @property
    def subject(self) -> str:
        """What an approval of it names (`approval.check`)."""
        return f"waiver:{self.id}"

    @property
    def digest(self) -> str:
        """Equal waivers, equal digest: the thing a person approves."""
        return canonical_digest(
            {
                "id": self.id,
                "record": self.record,
                "check": self.check,
                "reason": self.reason,
                "expires": self.expires,
                "granted": self.granted,
                "globs": list(self.globs),
            }
        )


def validate(w: Waiver) -> list[str]:
    """Why ``w`` is not a waiver ddflow will honour, one sentence each; [] when it is."""
    out = []
    if not w.id:
        out.append("a waiver needs an id")
    if not w.record:
        out.append("a waiver names the record it is for")
    if not w.reason.strip():
        out.append("a waiver needs a reason")
    if not w.globs:
        out.append("a waiver is scoped to files: give at least one glob")
    try:
        granted = clock.parse_date(w.granted)
    except ValueError:
        out.append(f"granted {w.granted!r} is not a date (YYYY-MM-DD)")
        return out
    try:
        expires = clock.parse_date(w.expires)
    except ValueError:
        out.append(f"expires {w.expires!r} is not a date (YYYY-MM-DD)")
        return out
    if expires < granted:
        out.append(f"expires {w.expires} is before it was granted ({w.granted})")
    elif expires > granted + timedelta(days=MAX_DAYS):
        out.append(
            f"a waiver lasts at most {MAX_DAYS} days: {w.expires} is later than {latest(granted)}"
        )
    return out


def latest(granted: date) -> str:
    """The last day a waiver granted on ``granted`` may run to."""
    return (granted + timedelta(days=MAX_DAYS)).isoformat()


def status(w: Waiver, today: date, approved: Callable[[Waiver], bool]) -> str:
    """``expired`` after its last day; else ``unapproved`` until a person approved this exact
    waiver; else ``active``. Expiry wins: an expired waiver is gone whoever approved it."""
    try:
        if today > clock.parse_date(w.expires):
            return EXPIRED
    except ValueError:
        return EXPIRED  # a waiver that cannot say when it ends covers nothing
    return ACTIVE if approved(w) else UNAPPROVED


def approved_in(state: Any) -> Callable[[Waiver], bool]:
    """The approval test against the folded log: a person approved this waiver's digest."""
    return lambda w: approval_check(state, w.subject, w.digest).ok


def covers(w: Waiver, res: CheckResult, finding: Finding) -> bool:
    """Does ``w`` cover ``finding`` of ``res``?"""
    return (
        w.record == res.record
        and (not w.check or w.check == res.check_id)
        and any(G.inside(finding.path, g) for g in w.globs)
    )


def apply(
    results: Iterable[CheckResult],
    waivers: Iterable[Waiver],
    *,
    today: date,
    approved: Callable[[Waiver], bool],
) -> list[CheckResult]:
    """``results`` with every finding an ACTIVE, valid waiver covers moved to ``waived``.
    A result whose findings are all covered becomes ``waived``; one partly covered stays
    ``fail`` for the rest. Waivers that are expired, unapproved or invalid cover nothing."""
    usable = [w for w in waivers if not validate(w) and status(w, today, approved) == ACTIVE]
    out = []
    for res in results:
        if res.status != FAIL:
            out.append(res)
            continue
        kept: list[Finding] = []
        covered: list[Finding] = []
        used: list[str] = []
        for f in res.findings:
            hit = next((w for w in usable if covers(w, res, f)), None)
            if hit is None:
                kept.append(f)
            else:
                covered.append(f)
                if hit.id not in used:
                    used.append(hit.id)
        if not covered:
            out.append(res)
            continue
        out.append(
            replace(
                res,
                status=FAIL if kept else WAIVED,
                findings=tuple(kept),
                waived=tuple(covered),
                waivers=tuple(used),
            )
        )
    return out


def resurfaced(waivers: Iterable[Waiver], today: date) -> list[Waiver]:
    """The waivers that ran out: their findings are live again, and brief and doctor say so.
    The oldest lapse first."""
    gone = []
    for w in waivers:
        try:
            if today > clock.parse_date(w.expires):
                gone.append(w)
        except ValueError:
            gone.append(w)
    return sorted(gone, key=lambda w: w.expires)


def expiring(waivers: Iterable[Waiver], today: date, within_days: int = 14) -> list[Waiver]:
    """Active-by-date waivers that run out within ``within_days``: a heads-up before they do."""
    soon = []
    for w in waivers:
        try:
            left = (clock.parse_date(w.expires) - today).days
        except ValueError:
            continue
        if 0 <= left <= within_days:
            soon.append(w)
    return sorted(soon, key=lambda w: w.expires)

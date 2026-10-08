"""Review dates and revisit triggers: guidance that stays current (B-uni-guidance-enforce).

Guidance is written once and read for years. Two things say it should be looked at again:

* its own ``review_by`` date has passed;
* a REVISIT TRIGGER fires: something outside it changed that it was written against (a new
  major of a library a decision governs, repeated violations, research that contradicts it).
  A trigger is a function registered here (`register_trigger`); P-decisions registers the
  concrete ones, this module only runs them.

Looking again ends in a recorded outcome: ``reaffirmed`` (still right: the next review date
moves out) or ``changed`` (it needs a successor: the date clears and the caller proposes a
superseding record for the operator -- never automatic). The same flow serves a rule and a
decision; neither has a review path of its own.

Pure: today and the world a trigger looks at are passed in.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from ...core import clock
from .record import GuidanceRecord

REAFFIRMED, CHANGED = "reaffirmed", "changed"
OUTCOMES = (REAFFIRMED, CHANGED)

#: How far out a reaffirmation puts the next review when the caller gives no interval.
DEFAULT_INTERVAL_DAYS = 180

#: A trigger: (record, world) -> why it fires, or "" when it does not.
Trigger = Callable[[GuidanceRecord, Any], str]
_TRIGGERS: dict[str, Trigger] = {}


def register_trigger(name: str, fn: Trigger) -> None:
    """Add a revisit trigger. A second registration of a name is an error."""
    if name in _TRIGGERS:
        raise ValueError(f"revisit trigger {name!r} is already registered")
    _TRIGGERS[name] = fn


def triggers() -> list[str]:
    return sorted(_TRIGGERS)


@dataclass(frozen=True)
class Due:
    """A piece of guidance that wants a look, and why."""

    record: GuidanceRecord
    reason: str
    #: Days past its review_by date (0 for a trigger with no date; a trigger sorts after
    #: a date that has passed, the most overdue first).
    overdue_days: int = 0
    trigger: str = ""


def review_due(records: Iterable[GuidanceRecord], today: date) -> list[Due]:
    """Live guidance whose ``review_by`` is today or earlier, most overdue first. A date that
    cannot be read is due (a review date nobody can read is no schedule at all)."""
    out = []
    for rec in records:
        if not rec.live or not rec.review_by:
            continue
        try:
            late = (today - clock.parse_date(rec.review_by)).days
        except ValueError:
            out.append(Due(rec, f"review_by {rec.review_by!r} is not a date", 0))
            continue
        if late >= 0:
            out.append(Due(rec, f"review_by {rec.review_by} has passed", late))
    return sorted(out, key=lambda d: (-d.overdue_days, d.record.id))


def revisit_due(records: Iterable[GuidanceRecord], world: Any) -> list[Due]:
    """Live guidance a registered trigger says to revisit. A trigger that raises is reported
    as a reason to look, not swallowed: it cannot be known not to apply."""
    out = []
    for rec in records:
        if not rec.live:
            continue
        for name, fn in sorted(_TRIGGERS.items()):
            try:
                why = fn(rec, world)
            except Exception as exc:
                why = f"trigger could not run ({type(exc).__name__}: {exc})"
            if why:
                out.append(Due(rec, why, 0, name))
    return sorted(out, key=lambda d: (d.record.id, d.trigger))


def due(records: Iterable[GuidanceRecord], today: date, world: Any = None) -> list[Due]:
    """Everything that wants a look: dated reviews first, then triggers, and a record that
    is due both ways appears once per reason."""
    records = list(records)
    return review_due(records, today) + revisit_due(records, world)


def reviewed(outcome: str, today: date, *, interval_days: int = DEFAULT_INTERVAL_DAYS) -> str:
    """The ``review_by`` a recorded review leaves: ``reaffirmed`` moves it ``interval_days``
    out from today; ``changed`` clears it (the successor carries its own date)."""
    if outcome not in OUTCOMES:
        raise ValueError(f"a review ends {REAFFIRMED} or {CHANGED}, not {outcome!r}")
    if interval_days < 1:
        raise ValueError("a review interval is at least one day")
    return (today + timedelta(days=interval_days)).isoformat() if outcome == REAFFIRMED else ""


def reminders(items: list[Due], limit: int = 5) -> list[str]:
    """Lines for brief and doctor, at most ``limit`` in all: the oldest review first, and
    when there are more than fit a last line counting the rest, so a long backlog is
    visible without filling the page."""
    if limit < 1:
        return []
    shown = items if len(items) <= limit else items[: limit - 1]
    lines = [
        f"{d.record.kind} {d.record.id}: {d.reason}"
        + (f" ({d.overdue_days} days)" if d.overdue_days else "")
        for d in shown
    ]
    if len(shown) < len(items):
        lines.append(f"... and {len(items) - len(shown)} more due for review")
    return lines

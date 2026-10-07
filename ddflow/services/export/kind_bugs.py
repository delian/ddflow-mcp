"""Bugs: Open (oldest first, with age), Fixed (newest first, with test and date), Invalid.

Filters: ``status`` (open | fixed | invalid; default all three), ``since`` (an ISO date:
open bugs found, fixed bugs fixed, invalid bugs closed on or after it), ``limit`` (at most
N entries per section) and ``phase`` (only bugs raised against that item or the tasks below
it: a phase id, or any task id, so a surface's ``--item`` maps onto it).
Unfiltered, a mature log is mostly fixed bugs; the filters exist so the document a human
or agent reads is the slice they asked for.

A bug has no title yet (B-bug-scope-event adds ``title``/``severity``/``scope``): they are
rendered when the record carries them, otherwise the id plus the first sentence of the
summary, clipped. "Age" is measured to the newest event in the log, never the clock, so
the same log renders the same bytes.
"""

from __future__ import annotations

import re
from typing import Any

from ...core import clock
from ...core.model import Bug
from . import registry
from .frame import one_line
from .query import ExportError, Query, _cutoff, by_id

STATUSES = ("open", "fixed", "invalid")
_SENTENCE = re.compile(r"(?<=[.!?])\s")


def first_sentence(text: str, limit: int = 140) -> str:
    """The first line's first sentence, whitespace-folded, clipped to ``limit``."""
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    return one_line(_SENTENCE.split(line, maxsplit=1)[0], limit)


def _day(ts: str) -> str:
    return ts[:10]


def _age_days(found: str, until: str) -> int | None:
    try:
        return max(0, (clock.parse_date(until[:10]) - clock.parse_date(found[:10])).days)
    except ValueError:
        return None


def _row(b: Bug, until: str) -> dict[str, Any]:
    tests = list(b.regression_tests) or ([b.regression_test] if b.regression_test else [])
    title = str(getattr(b, "title", "") or "")
    scope = str(getattr(b, "scope", "") or "")
    age = _age_days(b.found_at, until) if b.open else None
    return {
        "id": b.id,
        "title": one_line(title, 100),  # "" until B-bug-scope-event
        "severity": str(getattr(b, "severity", "") or ""),
        # the default scope (project) is not worth a tag on every line; show and
        # ``similar`` also name only a scope other than project
        "scope": "" if scope == "project" else scope,
        "summary": first_sentence(b.summary),
        "item": b.item,
        "found": _day(b.found_at),
        "age_days": -1 if age is None else age,
        "fixed": _day(b.fixed_at),
        "test": one_line(tests[0], 70) if tests else "",
        "more_tests": max(0, len(tests) - 1),
        "invalid": _day(b.invalid_at),
        "reason": one_line(b.invalid_reason, 100),
    }


def _data(q: Query, f: registry.Filters) -> dict[str, Any]:
    want = f.status or "all"
    if want != "all" and want not in STATUSES:
        raise ExportError(
            f"--status {f.status!r}: use one of {', '.join(STATUSES)}", registry.EXIT_REFUSED
        )
    scope: set[str] | None = None
    if f.phase:
        if q.item(f.phase) is None:
            raise ExportError(f"--phase {f.phase!r}: no such item", registry.EXIT_REFUSED)
        scope = {f.phase} | {t.id for t in q.tasks_under(f.phase)}
    until = q.events[-1].ts if q.events else ""
    bugs: list[Bug] = [b for b in q.bugs() if scope is None or b.item in scope]

    cut = _cutoff(f.since)  # refuses a malformed --since (exit 3), like every kind

    def since(ts: str) -> bool:  # an unreadable date cannot be shown to be in range
        return cut is None or bool(ts and cut(ts))

    opened = sorted(
        (b for b in bugs if b.open and since(b.found_at)), key=lambda b: (b.found_at, b.id)
    )
    fixed = sorted(
        (b for b in bugs if b.resolution == "fixed" and since(b.fixed_at)),
        key=lambda b: (b.fixed_at, by_id(b)),
        reverse=True,
    )
    invalid = sorted(
        (b for b in bugs if b.resolution == "invalid" and since(b.invalid_at)),
        key=lambda b: (b.invalid_at, by_id(b)),
        reverse=True,
    )
    sections = []
    for name, rows in (("open", opened), ("fixed", fixed), ("invalid", invalid)):
        if want not in ("all", name):
            continue
        shown = rows[: f.limit] if f.limit else rows
        sections.append(
            {
                "name": name,
                "label": name.capitalize(),
                "total": len(rows),
                "hidden": len(rows) - len(shown),
                "bugs": [_row(b, until) for b in shown],
            }
        )
    return {"sections": sections, "as_of": _day(until)}


registry.register(
    registry.DocKind(
        name="bugs",
        default_target="BUGS.md",
        data=_data,
        update_mode=registry.WHOLE,
        filters=frozenset({"since", "limit", "status", "phase"}),
        title="Bugs: open, fixed and invalid, with filters",
    )
)

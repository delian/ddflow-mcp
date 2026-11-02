"""Adoption plans order sessions by instant, not by timestamp text (Bff458ced2c).

`plan_adoptions` compared `e.ts <= o.ts` on the raw ISO strings. Text comparison puts a
no-fraction timestamp after a fractional one -- ``'2024-01-01T00:00:00Z' <=
'2024-01-01T00:00:00.500000Z'`` is False because ``'Z'`` sorts after ``'.'`` -- so a real
earlier start was treated as *after* a later orphan, and the orphan was adopted by a
still-earlier session.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.core.events import Event
from ddflow.services import sessions as S


def _ev(kind, subject, ts, data=None, id_=""):
    return Event(kind=kind, subject=subject, data=data or {}, ts=ts, id=id_)


def test_a_no_fraction_start_is_not_after_a_fractional_orphan():
    # s-nearest really starts (.000000Z) before the orphan (.500000Z); only the text
    # order disagrees. The orphan must be adopted by s-nearest, not s-early.
    events = [
        _ev("session.started", "s-early", "2023-12-31T23:59:59Z"),
        _ev("session.started", "s-nearest", "2024-01-01T00:00:00Z"),
        _ev(
            "session.prompt",
            "",
            "2024-01-01T00:00:00.500000Z",
            {"text": "lost prompt", "item": ""},
            "o1",
        ),
    ]
    assert S.plan_adoptions(events) == [
        (
            "session.prompt",
            "s-nearest",
            {
                "text": "lost prompt",
                "item": "",
                "adopted_from": "o1",
                "orphan_at": "2024-01-01T00:00:00.500000Z",
            },
        )
    ]


def test_precision_differences_do_not_change_the_nearest_session():
    # The same instant written with and without a fraction is one instant, so the
    # orphan still lands in its own (later) session rather than the earlier start.
    events = [
        _ev("session.started", "s-early", "2024-01-01T00:00:00.000000Z"),
        _ev("session.started", "s-later", "2024-01-01T01:00:00Z"),
        _ev(
            "session.note",
            "",
            "2024-01-01T01:00:00+00:00",
            {"text": "later note", "item": ""},
            "o2",
        ),
    ]
    (_, sid, _data) = S.plan_adoptions(events)[0]
    assert sid == "s-later"

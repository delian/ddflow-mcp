"""B6e15f963d2: a release must not re-display the releaser's own displaced claim.

C claims T (t=100); H claims it too (t=150, overlapping: a contest); H re-acquires (t=300),
which DISPLACES H's own earlier claim but leaves it in the contest. When H then releases,
`_redisplay` promoted the latest remaining contestant -- H's displaced claim -- so the item
went on showing H holding a lease H had just given up, and C's live claim was hidden. Its
own docstring says a displaced claim is never promoted. Both release forms are covered:
one naming the acquiring event (every release since B191) and the older holder-only one.
"""

from __future__ import annotations

import pytest

from ddflow.core.events import Event
from ddflow.core.model import fold


def _ev(kind: str, lamport: int, agent: str = "x", **data) -> Event:
    e = Event(kind=kind, subject="T", data=data, agent=agent, lamport=lamport, ts=f"t{lamport}")
    return Event(**{**e.__dict__, "id": e.compute_id()})


def _acq(lamport: int, holder: str, at: float) -> Event:
    return _ev("lease.acquired", lamport, holder, holder=holder, at=at, ttl_s=1000)


@pytest.mark.parametrize("names_event", [True, False], ids=["by-event", "holder-only"])
def test_the_releaser_is_not_shown_holding_the_item(names_event):
    c1, h1, h2 = _acq(2, "C", 100.0), _acq(3, "H", 150.0), _acq(4, "H", 300.0)
    data = {"holder": "H", **({"event": h2.id} if names_event else {})}
    it = fold([_ev("task.added", 1, title="t"), c1, h1, h2, _ev("lease.released", 5, "H", **data)])
    shown = it.items["T"].lease
    assert shown is not None and shown.holder == "C", shown
    assert shown.event == c1.id


def test_without_a_rival_the_release_leaves_the_item_unheld():
    h1, h2 = _acq(2, "H", 150.0), _acq(3, "H", 300.0)
    rel = _ev("lease.released", 4, "H", holder="H", event=h2.id)
    assert fold([_ev("task.added", 1, title="t"), h1, h2, rel]).items["T"].lease is None


def _at(lamport: int, holder: str, at: float, ttl: int) -> Event:
    return _ev("lease.acquired", lamport, holder, holder=holder, at=at, ttl_s=ttl)


@pytest.mark.parametrize("names_event", [True, False], ids=["by-event", "holder-only"])
def test_two_holders_each_releasing_after_reclaiming_leave_it_unheld(names_event):
    """Review finding: both contestants re-claimed, then both released -- nobody holds it."""
    c1, c2, h1, h2 = (
        _acq(2, "C", 100.0),
        _acq(3, "C", 150.0),
        _acq(4, "H", 200.0),
        _acq(5, "H", 250.0),
    )
    rel = [
        _ev("lease.released", 6, "C", holder="C", **({"event": c2.id} if names_event else {})),
        _ev("lease.released", 7, "H", holder="H", **({"event": h2.id} if names_event else {})),
    ]
    it = fold([_ev("task.added", 1, title="t"), c1, c2, h1, h2, *rel]).items["T"]
    assert it.lease is None or it.lease.holder not in ("C", "H"), it.lease


def test_a_takeover_victim_is_not_revived_as_the_releasers_claim():
    """Review finding: C's superseded claim was shown again after C released its latest."""
    c1, c2, b1 = _at(2, "c", 100.0, 50), _at(3, "c", 200.0, 50), _at(4, "b", 100.0, 50)
    rel = _ev("lease.released", 5, "c", holder="c", event=c2.id)
    it = fold([_ev("task.added", 1, title="t"), c1, c2, b1, rel]).items["T"]
    # c's superseded claim leaves the contest with the release (it stayed, on main), so
    # nothing of c's can be displayed again; b's unreleased claim is what remains.
    assert [h["holder"] for h in it.lease_contest] in ([], ["b"]), it.lease_contest
    assert it.lease is None or it.lease.holder == "b", it.lease


# -- D-contest-redisplay: a taken-over claim is displayed again, and says so ---------------


def _taken_over_then_released():
    """A and B overlap (a contest; B displayed); B lapses and C takes it over; C releases."""
    a, b, c = _at(2, "A", 100.0, 50), _at(3, "B", 120.0, 50), _at(4, "C", 300.0, 50)
    rel = _ev("lease.released", 5, "C", holder="C", event=c.id)
    return fold([_ev("task.added", 1, title="t"), a, b, c, rel]).items["T"], b


def test_releasing_a_takeover_displays_the_taken_over_contestant_again():
    """The rule on main, kept by the operator: an unresolved contest always shows a live
    claim, so the latest contestant -- B, though C took it over -- is displayed again."""
    it, b = _taken_over_then_released()
    assert it.lease is not None and it.lease.holder == "B" and it.lease.event == b.id
    assert it.lease_taken_over_by() == "C"


def test_a_claim_nobody_took_over_is_not_marked():
    c1, h1 = _acq(2, "C", 100.0), _acq(3, "H", 150.0)
    it = fold([_ev("task.added", 1, title="t"), c1, h1]).items["T"]
    assert it.lease is not None and it.lease_taken_over_by() == ""


def test_show_and_status_mark_the_redisplayed_claim(monkeypatch):
    from types import SimpleNamespace

    from ddflow.surfaces.commands import reporting as R

    it, _ = _taken_over_then_released()
    assert "previously taken over by C" in R.taken_over_note(it)
    from collections import defaultdict

    render = defaultdict(list, {"running": [it], "parallel": ""})
    lines = R._queue_lines(render)
    assert any("previously taken over by C" in ln for ln in lines), lines
    assert R.taken_over_note(SimpleNamespace(lease=None)) == ""


def test_the_redisplay_docstring_states_the_rule():
    from ddflow.core.handlers.leases import _redisplay

    doc = " ".join((_redisplay.__doc__ or "").split())
    # It said "a displaced claim is never promoted" -- false for a contestant taken over.
    assert "A displaced claim is never promoted" not in doc
    assert "displayed again" in doc and "taken over" in doc.lower()

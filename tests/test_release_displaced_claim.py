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

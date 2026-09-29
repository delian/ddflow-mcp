"""A lease contest names only the claims that were live at once (bug Ba73ee6ea72).

Alice claims at 1000 for 1000s; Bob, offline, at 1900 -- inside her window, a real
contest. Carol claimed at 1200 for 100s: she overlapped Alice, but was over 600s before
Bob began. The fold joined her into the WHOLE contest because she overlapped one
contestant, so `show` said "claimed at once by alice and bob and carol" and
`resolve --keep bob` released Carol as having lost to a claim she never met.
"""

from __future__ import annotations

from itertools import permutations
from pathlib import Path

import pytest

from ddflow.core.events import Event
from ddflow.core.model import fold
from ddflow.infra.log import EventLog


def _ev(kind: str, subject: str, lamport: int, agent: str = "x", **data) -> Event:
    e = Event(kind=kind, subject=subject, data=data, agent=agent, lamport=lamport, ts=f"t{lamport}")
    return Event(**{**e.__dict__, "id": e.compute_id()})


def _acq(lamport: int, holder: str, at: float, ttl: int) -> Event:
    return _ev("lease.acquired", "T", lamport, holder, holder=holder, at=at, ttl_s=ttl)


_CLAIMS = {"alice": (1000.0, 1000), "bob": (1900.0, 1000), "carol": (1200.0, 100)}


def _overjoin(order: tuple[str, ...]) -> list[Event]:
    """The three claims, folded in ``order`` (fold order is Lamport, not wall time)."""
    return [
        _ev("task.added", "T", 1, title="t"),
        *(_acq(2 + i, who, *_CLAIMS[who]) for i, who in enumerate(order)),
    ]


@pytest.mark.parametrize("order", list(permutations(_CLAIMS)), ids="-".join)
def test_a_claim_joins_only_the_claims_its_window_overlaps(order):
    """FAILED before the fix, in the fold orders where Carol arrives while Alice is
    contested or displayed: she was joined into Alice and Bob's contest."""
    it = fold(_overjoin(order)).items["T"]
    assert sorted(h["holder"] for h in it.lease_contest) == ["alice", "bob"]
    assert it.lease.holder == "bob"
    assert "carol" not in it.contest_summary()


def test_resolve_keeping_bob_releases_only_the_claim_that_met_his(repo: Path):
    log = EventLog(repo, "op")
    log.shard.parent.mkdir(parents=True, exist_ok=True)
    events = _overjoin(("alice", "bob", "carol"))
    log.shard.write_text("".join(ev.to_json() + "\n" for ev in events))
    from ddflow.api import items

    out = items.resolve(repo, "T", keep="bob", agent="op")
    assert out.exit == 0, out.reason
    assert out.data["released"] == ["alice"]


@pytest.mark.parametrize("order", [("carol", "dave"), ("dave", "carol")], ids="-".join)
def test_a_claim_still_joins_a_contest_the_displayed_lease_is_not_in(order):
    """Alice and Bob collided and both lapsed; Carol took the item over clean. Dave's
    claim overlapped Alice and Bob -- part of THAT contest, whichever folds first -- though
    it ended before Carol's began."""
    claims = {"carol": (6000.0, 1800), "dave": (1500.0, 1800)}
    it = fold(
        [
            _ev("task.added", "T", 1, title="t"),
            _acq(2, "alice", 1000.0, 1800),
            _acq(3, "bob", 1100.0, 1800),
            *(_acq(4 + i, who, *claims[who]) for i, who in enumerate(order)),
        ]
    ).items["T"]
    assert sorted(h["holder"] for h in it.lease_contest) == ["alice", "bob", "dave"]
    assert it.lease.holder == "carol"

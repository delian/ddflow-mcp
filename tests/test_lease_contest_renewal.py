"""Reclaims, long handover histories and renewals by contestants.

Split from tests/test_lease_contest.py (rule r-small-files); helpers live in lease_contest_support.py.
"""

from __future__ import annotations

import random

import pytest
from helpers import make_event as _ev
from lease_contest_support import (
    _acq,
    _contest,
    _history,
    _holders,
    _interleave,
    _shown,
)

from ddflow.core.events import Event
from ddflow.core.model import fold


def test_a_holders_own_reclaim_keeps_its_first_claim_on_record():
    """B6ac30c2acb, FAILED before the fix: A re-claimed the item after its first claim
    lapsed, and the fold simply replaced the first claim. C overlapped only that first
    window, so C folded as clean history and the double claim was gone."""
    a1, a2 = _acq(2, "a", 300.0, 50), _acq(3, "a", 700.0, 100)
    c = _acq(4, "c", 340.0, 20)
    events = [_ev("task.added", "T", 1, title="t"), a1, a2, c]
    assert _contest(events) == {a1.id, c.id}
    it = fold(events).items["T"]
    assert it.lease.event == a2.id


def test_a_holders_own_reclaim_contests_both_of_its_claims_with_a_rival():
    """B6ac30c2acb's repro: C overlapped both of A's windows; A's first claim was
    nowhere -- neither in the contest nor on the record."""
    a1, a2 = _acq(1, "a", 300.0, 50), _acq(2, "a", 400.0, 100)
    c = _acq(3, "c", 350.0, 300)
    assert _contest([_ev("task.added", "T", 0, title="t"), a1, a2, c]) == {a1.id, a2.id, c.id}


def test_a_claim_nine_handovers_back_is_still_weighed():
    """B7164b23643, FAILED before the fix: MAX_DISPLACED=8 forgot A after ten clean TTL
    takeovers, so K -- who overlapped only A -- folded to no contest and was recorded
    as displaced by J, a claim it never met."""
    events = [_ev("task.added", "T", 0, title="t")]
    names = "abcdefghij"
    for i, who in enumerate(names):
        events.append(_acq(1 + i, who, 1000.0 + 100 * i, 90))
    k = _acq(100, "k", 1050.0, 10)
    events.append(k)
    it = fold(events).items["T"]
    assert _holders(it.lease_contest) == ["a", "k"]
    assert it.lease.holder == "j"


def test_a_renewal_by_a_contestant_that_is_not_displayed_widens_its_window():
    """Bee8e21c547, FAILED before the fix: A's renewal at 1612 kept her live to 2412, but
    she was a contestant, not the displayed lease and not displaced, so the renewal was
    dropped -- and C, inside her renewed window, was never contested with her."""
    b = _acq(2, "b", 1900.0, 50)
    a = _acq(3, "a", 1150.0, 800)
    renew = _ev("lease.renewed", "T", 4, "a", holder="a", at=1612.0)
    c = _acq(5, "c", 2050.0, 50)
    events = [_ev("task.added", "T", 1, title="t"), b, a, renew, c]
    assert _contest(events) == {a.id, b.id, c.id}


@pytest.mark.parametrize("renewals", [True, False], ids=["renewals", "no-renewals"])
@pytest.mark.parametrize("seed", range(60))
def test_no_claim_that_overlapped_another_is_ever_missing_from_the_contest(seed, renewals):
    """Property, the invariant all three gaps broke: with re-claims by one holder,
    renewals folded anywhere after their claim, and histories longer than eight
    handovers, the contest is exactly the claims whose window -- widened by its
    renewals -- overlapped another holder's, in every fold order sampled. Exactly, with
    renewals too: B-late-renewal-overjoin, FAILED before its fix, joined the lease
    displayed when a late renewal folded, whether or not it overlapped anyone."""
    rnd = random.Random(5000 + seed)
    claims, streams = _history(rnd, renewals)
    want = {
        e
        for w, e, s, t in claims
        if any(w2 != w and s <= t2 and s2 <= t for w2, _e2, s2, t2 in claims)
    }
    for _ in range(25):
        events = _interleave(rnd, streams)
        it = fold(events).items["T"]
        got = {h["event"] for h in it.lease_contest}
        trace = (claims, [(e.kind, e.agent, e.data.get("at")) for e in events])
        assert got == want, trace
        if got and it.lease is not None and it.lease.event not in got:
            # A displayed lease that overlapped nobody is not contested, and keeping it
            # settles the whole contest (B-resolve-cannot-keep-holder).
            assert {h["event"] for h in it.lease_losers(_shown(it))} == got, trace


def test_a_renewal_of_the_displayed_lease_is_weighed_against_the_record():
    """X's expiry was recorded, so Y -- claimed earlier in another clone -- folded as a
    clean takeover and X went on the record. Y's renewals, folded after, kept Y live past
    the moment X claimed: a double claim, which must not depend on whether the renewals
    happened to fold before X."""
    x, y = _acq(2, "x", 1000.0, 100), _acq(4, "y", 500.0, 100)
    expired = _ev("lease.expired", "T", 3, "op", holder="x")
    renewals = [
        _ev("lease.renewed", "T", 5 + i, "y", holder="y", at=at)
        for i, at in enumerate((590.0, 680.0, 770.0, 860.0, 950.0))
    ]
    late = [_ev("task.added", "T", 1, title="t"), x, expired, y, *renewals]
    assert _contest(late) == {x.id, y.id}


# -- what the record does NOT do: pinned after review findings that assumed otherwise ---


def test_a_holders_own_overlapping_reclaim_is_not_a_contest():
    """A claim never contests its own holder's: the earlier claim goes on the record,
    and the item is not contested (review finding, refuted by this fold)."""
    a1, a2 = _acq(2, "a", 0.0, 100), _acq(3, "a", 50.0, 100)
    it = fold([_ev("task.added", "T", 1, title="t"), a1, a2]).items["T"]
    assert it.lease_contest == [] and it.lease.event == a2.id
    assert [e["event"] for e in it.displaced] == [a1.id]


@pytest.mark.parametrize("renew_first", [True, False], ids=["renewal-first", "release-first"])
def test_a_released_claim_is_not_revived_by_a_late_renewal_of_its_rival(renew_first):
    """H2 took the item over by TTL from H1 and later released it. H1's late renewal
    proves it was still live when H2 claimed -- but a released claim is withdrawn from
    every contest, so whichever folds first, nothing is contested. On main this
    depended on the order: the release first left a contest naming the released claim
    and displaying none."""
    h1, h2 = _acq(2, "h1", 0.0, 10), _acq(3, "h2", 15.0, 10)
    renew = _ev("lease.renewed", "T", 0, "h1", holder="h1", at=8.0)
    release = _ev("lease.released", "T", 0, "h2", holder="h2", event=h2.id)
    tail = [renew, release] if renew_first else [release, renew]
    tail = [Event(**{**e.__dict__, "lamport": 4 + i}) for i, e in enumerate(tail)]
    it = fold([_ev("task.added", "T", 1, title="t"), h1, h2, *tail]).items["T"]
    assert it.lease_contest == []
    on_record = {e["event"] for e in it.displaced} | ({it.lease.event} if it.lease else set())
    assert on_record == {h1.id}


def test_a_renewal_belongs_to_the_holders_latest_claim_before_it():
    """A holder id is one clone's, and its claims follow one another there: H's renewal
    at 14, after H re-claimed at 12, renews that second claim -- not the first, which
    had lapsed at 10. So the first is not contested with G's claim at 13."""
    h1, h2, g = _acq(2, "h", 0.0, 10), _acq(3, "h", 12.0, 10), _acq(4, "g", 13.0, 10)
    renew = _ev("lease.renewed", "T", 5, "h", holder="h", at=14.0)
    it = fold([_ev("task.added", "T", 1, title="t"), h1, h2, g, renew]).items["T"]
    assert {h["event"] for h in it.lease_contest} == {h2.id, g.id}
    widened = next(h for h in it.lease_contest if h["event"] == h2.id)
    assert widened["lease"]["renewed_at"] == 14.0

"""Releases: a release takes one claim off the record, never the claims left.

Split from tests/test_lease_contest.py (rule r-small-files); helpers live in lease_contest_support.py.
"""

from __future__ import annotations

import random
from itertools import permutations
from pathlib import Path

import pytest
from helpers import make_event as _ev
from lease_contest_support import (
    _GROUPS,
    _KEEP_FAR,
    _acq,
    _folded,
    _holders,
    _partners,
    _run,
    _Seq,
)

from ddflow.core.events import Event
from ddflow.core.model import fold
from ddflow.infra.log import EventLog


def test_a_release_that_ends_a_contest_keeps_the_claims_left_on_record():
    """B4cefa40964, FAILED before the fix: with R's claim released, C was dropped from the
    contest and kept nowhere, so N -- who overlapped C -- went unreported."""
    claims = {"c": (1000.0, 1000), "r": (1500.0, 1000), "l": (3000.0, 1000), "n": (1200.0, 1000)}
    seq = [("acq", "c"), ("acq", "r"), ("acq", "l"), ("rel", "r"), ("acq", "n")]
    it = _run(claims, seq, holders={"l": "r"})
    assert _holders(it.lease_contest) == ["c", "n"]


_THREE = {"A": (1000.0, 2000), "B": (2000.0, 2000), "C": (3500.0, 1500)}


@pytest.mark.parametrize("order", list(permutations(_THREE)), ids="-".join)
def test_releasing_the_displayed_contestant_displays_the_latest_left(order):
    """B21c7858371, FAILED before the fix: the item was left with no lease at all, though
    the log without C displays B."""
    it = _run(_THREE, [*(("acq", w) for w in order), ("rel", "C")])
    assert _holders(it.lease_contest) == ["A", "B"] and it.lease.holder == "B"


@pytest.mark.parametrize("seed", range(40))
def test_releases_in_any_position_leave_the_contest_exact(seed):
    """Property, releases and resolutions folded at random points: no released claim is
    anywhere, every other claim is still on record (contest, displaced or displayed),
    the contest is exactly the unreleased claims with an unreleased overlap partner --
    so no unreleased pair that overlapped is ever out of it -- and a standing contest
    always displays a claim. A resolution releases every partner of the kept claim, so
    the same oracle holds after one -- dated inside the claims or after all of them, when
    the kept claim, lapsed, holds the item in a fresh window that meets nobody while its
    own window stays on the record (B6805b48aca: stretched over the gap instead, it hid
    the claims taken there)."""
    rnd = random.Random(1000 + seed)
    names = ["a", "b", "c", "d", "e"][: rnd.choice([3, 4, 5])]
    claims = {w: (float(rnd.randrange(0, 40) * 50), rnd.choice([50, 100, 300, 800])) for w in names}
    for _ in range(60):
        order = rnd.sample(names, len(names))
        seq = _Seq(("acq", w) for w in order)
        seq.gone = {w for w in names if rnd.random() < 0.35}
        for w in list(seq.gone):
            seq.insert(rnd.randrange(seq.index(("acq", w)) + 1, len(seq) + 1), ("rel", w))
        for _ in range(rnd.choice([0, 1, 2])):
            step = rnd.choice(["res", "res", "hold"])
            seq.insert(rnd.randrange(len(seq) + 1), (step, rnd.randrange(5)))
        at = rnd.choice([0.0, 10_000.0])
        it = _run(claims, seq, at=at)
        gone = seq.gone
        left = {w: claims[w] for w in names if w not in gone}
        partners = _partners(left)
        on_record = {h["holder"] for h in it.lease_contest} | {e["holder"] for e in it.displaced}
        on_record |= {it.lease.holder} if it.lease else set()
        assert on_record == set(left), (claims, seq)
        assert _holders(it.lease_contest) == sorted(w for w in left if partners[w]), (claims, seq)
        assert it.lease is not None or not it.lease_contest, (claims, seq)
        for e in it.displaced:  # a window on record is one the claim really had: its
            # own, or the fresh one a resolution started (since displaced by another)
            got = (e["lease"]["acquired_at"], e["lease"]["ttl_s"])
            assert got in (claims[e["holder"]], (at, claims[e["holder"]][1])), (claims, seq)


@pytest.mark.parametrize(
    ("claims", "keep", "order"),
    [(_KEEP_FAR, "K", o) for o in permutations(_KEEP_FAR)]
    + [(_GROUPS, "alice", o) for o in permutations(_GROUPS)],
    ids=lambda v: "-".join(v) if isinstance(v, tuple) else (v if isinstance(v, str) else ""),
)
def test_resolve_keeps_every_claim_it_did_not_release_on_record(claims, keep, order):
    """FAILED before the fix: the resolution cleared the contest and overwrote the lease,
    so X -- whose one partner, Y, was released as the holder K takes over from -- was
    kept nowhere, and a later claim overlapping X would have gone unreported."""
    it = _folded(claims, order)
    kept = next(h for h in it.lease_contest if h["holder"] == keep)
    losers = it.lease_losers(kept)
    after = [
        *(
            _ev("lease.released", "T", 20 + i, "op", holder=h["holder"], event=h["event"])
            for i, h in enumerate(losers)
        ),
        Event("item.resolved", "T", {"kind": "task", "claim": kept, "at": 9000.0}, "op", 30),
    ]
    it = _folded(claims, order, *after)
    left = {w: claims[w] for w in claims if w not in {h["holder"] for h in losers}}
    standing = sorted(w for w, others in _partners(left).items() if others)
    on_record = {e["holder"] for e in it.displaced} | {it.lease.holder}
    assert _holders(it.lease_contest) == standing and it.lease.holder == keep
    # Displayed. Having lapsed long before the resolution (at 9000), it holds the item in
    # a fresh window, and its own window is on the record as history that fresh window
    # displaced -- never another claim (D-resolve-fresh-window).
    assert all(e["by"]["holder"] == keep for e in it.displaced if e["holder"] == keep)
    assert it.lease.acquired_at == 9000.0
    assert on_record | set(standing) == set(left)


_BYSTANDERS = {"a": (900.0, 100), "b": (300.0, 100), "c": (300.0, 100), "d": (100.0, 800)}


@pytest.mark.parametrize("order", list(permutations(_BYSTANDERS)), ids="-".join)
def test_resolve_leaves_bystanders_that_met_each_other_contested(repo: Path, order):
    """Bb8af177fcc, FAILED before the fix: keeping A released D, its one partner, and then
    displaced B and C "by A" -- a claim neither met -- so their own overlap was no longer
    a contest and `resolve` refused them as "not contested"."""
    log = EventLog(repo, "op")
    log.shard.parent.mkdir(parents=True, exist_ok=True)
    events = [
        _ev("task.added", "T", 1, title="t"),
        *(_acq(2 + i, w, *_BYSTANDERS[w]) for i, w in enumerate(order)),
    ]
    log.shard.write_text("".join(ev.to_json() + "\n" for ev in events))
    from ddflow.api import items

    def displaced(it) -> list[tuple[str, str]]:
        return sorted((e["holder"], e["event"]) for e in it.displaced if e["holder"] in "bc")

    before = displaced(fold(log.read_all()).items["T"])  # late history, by acquisition
    out = items.resolve(repo, "T", keep="a", agent="op")
    assert out.exit == 0, out.reason
    assert out.data["released"] == ["d"]
    it = fold(log.read_all()).items["T"]
    assert _holders(it.lease_contest) == ["b", "c"] and it.lease.holder == "a"
    assert displaced(it) == before  # the resolution did not file them as history
    assert "b (" in it.contest_summary() and "c (" in it.contest_summary()
    assert items.resolve(repo, "T", keep="b", agent="op").exit == 0

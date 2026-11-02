"""A resolution keeping a LAPSED claim starts a fresh window (D-resolve-fresh-window).

Split from tests/test_lease_contest.py (rule r-small-files); helpers live in lease_contest_support.py.
"""

from __future__ import annotations

from itertools import permutations
from pathlib import Path

import pytest
from helpers import make_event as _ev
from lease_contest_support import (
    _KEEP_FAR,
    _acq,
    _folded,
    _holders,
    _resolved,
)

from ddflow.core.events import Event
from ddflow.core.model import fold
from ddflow.infra.log import EventLog

# -- a resolution keeping a LAPSED claim starts a fresh window (D-resolve-fresh-window) --

_GAP = {"A": (1150.0, 300), "E": (1200.0, 300), "C": (150.0, 150), "D": (250.0, 50)}


@pytest.mark.parametrize("order", list(permutations(_GAP)), ids="-".join)
def test_keeping_a_lapsed_claim_does_not_stretch_it_over_the_gap(order):
    """B6805b48aca, FAILED before the fix: D (250..300) kept at 5000 was displayed over
    250..5050, so A's claim at 1150 -- a takeover long after D lapsed -- lay inside it
    and was filed as history. The kept claim now holds the item from the resolution on,
    its real tenure stays on the record, and A, which met neither, is no contestant."""
    it, kept, rel = _resolved(_GAP, order, "D", 5000.0)
    assert sorted(e.data["holder"] for e in rel) == ["C", "E"]
    assert it.lease.holder == "D" and it.lease.event == kept["event"]
    assert (it.lease.acquired_at, it.lease.renewed_at) == (5000.0, 5000.0)
    assert it.lease_contest == [] and not it.contest_summary()
    on_record = {(e["holder"], e["lease"]["acquired_at"]) for e in it.displaced}
    assert on_record == {("A", 1150.0), ("D", 250.0)}, "D's own tenure stays on record"


@pytest.mark.parametrize("order", list(permutations(_GAP)), ids="-".join)
def test_a_late_claim_inside_the_kept_claims_real_tenure_is_still_a_contest(order):
    """What the stretched window used to catch must still be caught: F, from a clone
    that saw none of this, claimed at 260 -- while D really held the item."""
    late = _ev("lease.acquired", "T", 40, "F", holder="F", at=260.0, ttl_s=100)
    it, _kept, _rel = _resolved(_GAP, order, "D", 5000.0, late)
    assert _holders(it.lease_contest) == ["D", "F"]
    assert it.lease.holder == "D" and it.lease.acquired_at == 5000.0


@pytest.mark.parametrize("order", list(permutations(_GAP)), ids="-".join)
def test_a_claim_inside_the_fresh_window_is_a_contest_and_one_in_the_gap_is_not(order):
    """Only claims overlapping the fresh window are re-weighed against it: G at 5020
    overlaps D's 5000..5050; H at 3000 sits in the gap and meets nobody."""
    g = _ev("lease.acquired", "T", 40, "G", holder="G", at=5020.0, ttl_s=100)
    h = _ev("lease.acquired", "T", 41, "H", holder="H", at=3000.0, ttl_s=100)
    it, _kept, _rel = _resolved(_GAP, order, "D", 5000.0, h, g)
    assert _holders(it.lease_contest) == ["D", "G"]


def test_a_late_renewal_in_the_gap_widens_the_kept_claims_real_tenure():
    """D's own clone renewed at 1300 -- so D DID hold it then, and A at 1150 met it. The
    renewal widens D's original window, not the fresh one."""
    renew = _ev("lease.renewed", "T", 40, "D", holder="D", at=1300.0)
    it, _kept, _rel = _resolved(_GAP, ("A", "E", "C", "D"), "D", 5000.0, renew)
    assert _holders(it.lease_contest) == ["A", "D"]
    assert (it.lease.acquired_at, it.lease.renewed_at) == (5000.0, 5000.0)


def test_a_renewal_of_the_fresh_lease_does_not_stretch_the_original():
    renew = _ev("lease.renewed", "T", 40, "D", holder="D", at=5040.0)
    it, _kept, _rel = _resolved(_GAP, ("A", "E", "C", "D"), "D", 5000.0, renew)
    assert it.lease.renewed_at == 5040.0 and it.lease_contest == []
    orig = next(e for e in it.displaced if e["holder"] == "D")
    assert orig["lease"]["renewed_at"] == 250.0


def test_releasing_the_kept_claim_ends_the_fresh_lease_and_its_record():
    it, _kept, _rel = _resolved(
        _GAP,
        ("A", "E", "C", "D"),
        "D",
        5000.0,
        _ev("lease.released", "T", 40, "D", holder="D", event=_acq(5, "D", 250.0, 50).id),
    )
    assert it.lease is None or it.lease.holder != "D"
    assert "D" not in {e["holder"] for e in it.displaced}


def test_keeping_a_claim_still_live_extends_it_as_before():
    """No gap, nothing to start afresh: X (1000..2000) kept at 1500 runs on from there."""
    it, _kept, _rel = _resolved(_KEEP_FAR, tuple(_KEEP_FAR), "X", 1500.0)
    assert (it.lease.acquired_at, it.lease.renewed_at) == (1000.0, 1500.0)
    assert "X" not in {e["holder"] for e in it.displaced}


def test_a_late_renewal_of_the_fresh_window_widens_it_not_the_original():
    """Reviewer probe: once G has taken the item from D's fresh window, a renewal from D's
    clone at 5040 belongs to the fresh window (its latest claim before then) -- the
    original must not be stretched back over the gap."""
    g = _ev("lease.acquired", "T", 40, "G", holder="G", at=5020.0, ttl_s=100)
    renew = _ev("lease.renewed", "T", 41, "D", holder="D", at=5040.0)
    it, _kept, _rel = _resolved(_GAP, ("A", "E", "C", "D"), "D", 5000.0, g, renew)
    windows = {
        e["lease"]["acquired_at"]: e["lease"]["renewed_at"]
        for e in [*it.lease_contest, *it.displaced]
        if e["holder"] == "D"
    }
    assert windows == {250.0: 250.0, 5000.0: 5040.0}, windows
    assert "A" not in _holders(it.lease_contest)


def test_keeping_a_claim_whose_expiry_was_recorded_gives_it_a_live_window():
    """roborev: an expiry zeroed the claim's TTL, and the fresh window inherited 0 s --
    the kept holder held nothing."""
    exp = _ev("lease.expired", "T", 10, "op", holder="D")
    it = _folded(_GAP, ("A", "E", "C", "D"), exp)
    kept = next(h for h in it.lease_candidates() if h["holder"] == "D")
    assert kept["lease"]["ttl_s"] == 0
    res = Event(
        "item.resolved", "T", {"kind": "task", "claim": kept, "at": 5000.0, "ttl_s": 900}, "op", 30
    )
    rel = [
        _ev("lease.released", "T", 20 + i, "op", holder=h["holder"], event=h["event"])
        for i, h in enumerate(it.lease_losers(kept))
    ]
    it = _folded(_GAP, ("A", "E", "C", "D"), exp, *rel, res)
    assert it.lease.holder == "D" and it.lease.ttl_s == 900 and not it.lease.expired_at
    assert not it.lease.expired(5100.0)


def test_resolve_carries_the_configured_ttl_to_a_fresh_window(repo: Path):
    """roborev: the payload's TTL had no test of its writer -- an operator's `[lease]
    ttl_s` must reach a kept claim whose recorded expiry zeroed its own."""
    log = EventLog(repo, "op")
    log.shard.parent.mkdir(parents=True, exist_ok=True)
    (repo / ".ddflow" / "config.toml").write_text("[lease]\nttl_s = 77\n")
    events = [
        _ev("task.added", "T", 1, title="t"),
        *(_acq(2 + i, w, *_GAP[w]) for i, w in enumerate(_GAP)),
        _ev("lease.expired", "T", 10, "op", holder="D"),
    ]
    log.shard.write_text("".join(ev.to_json() + "\n" for ev in events))
    from ddflow.api import items

    out = items.resolve(repo, "T", keep="D", agent="op")
    assert out.exit == 0, out.reason
    it = fold(log.read_all()).items["T"]
    assert it.lease.holder == "D" and it.lease.ttl_s == 77 and not it.lease.expired_at

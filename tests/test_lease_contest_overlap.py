"""A lease contest keeps every claim that overlapped another, and says who met whom.

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
    _ORDERS,
    _REPRO,
    _acq,
    _folded,
    _holders,
    _partners,
    _shown,
)

from ddflow.core.model import fold
from ddflow.infra.log import EventLog


@pytest.mark.parametrize("order", _ORDERS, ids="-".join)
def test_every_claim_that_overlapped_another_stays_in_the_contest(order):
    """FAILED on d870e8c in every order (Carol dropped), and before it in bob-carol-alice
    and carol-bob-alice: Carol was displaced before Alice arrived, and a new claim was
    never compared with a displaced one."""
    it = _folded(_REPRO, order)
    assert _holders(it.lease_contest) == ["alice", "bob", "carol"]
    assert it.lease.holder == "bob"


@pytest.mark.parametrize("order", _ORDERS, ids="-".join)
def test_the_contest_names_who_overlapped_whom(order):
    """FAILED before the fix: "claimed at once by alice and bob and carol"."""
    it = _folded(_REPRO, order)
    text = it.contest_summary()
    assert "claimed at once" not in text
    clauses = {c.split(" (")[0]: c for c in text.split(": ", 1)[1].split("; ")}
    for holder, others in _partners(_REPRO).items():
        mentioned = {o for o in others if o in clauses.get(holder, "")}
        mentioned |= {o for o in others if holder in clauses.get(o, "")}
        assert mentioned == others, text
    assert "bob" not in clauses.get("carol", "") and "carol" not in clauses.get("bob", "")


@pytest.mark.parametrize(
    ("keep", "released"),
    [("bob", ["alice"]), ("alice", ["bob", "carol"]), ("carol", ["alice", "bob"])],
)
def test_resolve_releases_only_the_claims_that_met_the_kept_one(repo: Path, keep, released):
    """FAILED before the fix for bob: Carol was released though she never met him. Keeping
    Carol releases Bob although she never met him either -- he holds the item she is
    being given."""
    log = EventLog(repo, "op")
    log.shard.parent.mkdir(parents=True, exist_ok=True)
    it_events = [
        _ev("task.added", "T", 1, title="t"),
        *(_acq(2 + i, who, *_REPRO[who]) for i, who in enumerate(_REPRO)),
    ]
    log.shard.write_text("".join(ev.to_json() + "\n" for ev in it_events))
    from ddflow.api import items

    out = items.resolve(repo, "T", keep=keep, agent="op")
    assert out.exit == 0, out.reason
    assert sorted(out.data["released"]) == released
    it = fold(log.read_all()).items["T"]
    assert it.lease_contest == [] and it.lease.holder == keep


def test_a_release_that_leaves_no_two_claims_overlapping_ends_the_contest():
    """FAILED before the fix: with Alice gone, Bob and Carol -- who never met -- were
    still 'contested', because the contest ended only at one claim left."""
    it = _folded(_REPRO, list(_REPRO), _ev("lease.released", "T", 9, "alice", holder="alice"))
    assert it.lease_contest == [] and it.lease.holder == "bob"


@pytest.mark.parametrize("order", _ORDERS, ids="-".join)
def test_a_late_renewal_widens_the_contested_claims_window(order):
    """Carol renewed at 1850, so she WAS live when Bob claimed: she met him after all,
    and resolving for Bob must release her."""
    renew = _ev("lease.renewed", "T", 50, "carol", holder="carol", at=1850.0)
    it = _folded(_REPRO, order, renew)
    bob = next(h for h in it.lease_contest if h["holder"] == "bob")
    assert _holders(it.lease_losers(bob)) == ["alice", "carol"]


def test_a_renewal_of_the_displayed_lease_widens_its_contested_window():
    """FAILED before the fix: Bob's renewal moved the displayed lease but not his entry in
    the contest, so Dave -- who claimed inside the renewed window -- read as never having
    met him."""
    claims = {**_REPRO, "dave": (3200.0, 100)}
    renew = _ev("lease.renewed", "T", 5, "bob", holder="bob", at=2800.0)
    it = fold(
        [
            _ev("task.added", "T", 1, title="t"),
            *(_acq(2 + i, who, *claims[who]) for i, who in enumerate(("alice", "bob"))),
            renew,
            _acq(6, "dave", *claims["dave"]),
        ]
    ).items["T"]
    bob = next(h for h in it.lease_contest if h["holder"] == "bob")
    assert _holders(it.lease_clashes(bob)) == ["alice", "dave"]


_CHAIN = {"a": (1000.0, 600), "b": (500.0, 300), "c": (1700.0, 350), "d": (650.0, 750)}


@pytest.mark.parametrize("order", list(permutations(_CHAIN)), ids="-".join)
def test_a_chain_keeps_every_claim_that_overlapped_one_in_it(order):
    """B6f34894078, FAILED on d870e8c for a-d-b-c and others: B overlapped only D, which
    was contested, not displayed -- and the late-claim guard left B out."""
    it = _folded(_CHAIN, order)
    assert _holders(it.lease_contest) == ["a", "b", "d"]
    assert it.lease.holder == "c"


@pytest.mark.parametrize("order", list(permutations(_GROUPS)), ids="-".join)
def test_two_groups_that_never_met_are_named_apart(order):
    """B11e15914bb: FAILED before the fix -- some orders gave [alice, dave] only, others
    all four "claimed at once". Both groups stay, and neither is said to meet the other."""
    it = _folded(_GROUPS, order)
    assert _holders(it.lease_contest) == ["alice", "bob", "carol", "dave"]
    for h in it.lease_contest:
        assert {g["holder"] for g in it.lease_clashes(h)} == _partners(_GROUPS)[h["holder"]]


@pytest.mark.parametrize("order", [("carol", "dave"), ("dave", "carol")], ids="-".join)
def test_a_claim_still_joins_a_contest_the_displayed_lease_is_not_in(order):
    """Alice and Bob collided and both lapsed; Carol took the item over clean. Dave's
    claim overlapped Alice and Bob -- part of THAT contest, whichever folds first -- though
    it ended before Carol's began."""
    claims = {"alice": (1000.0, 1800), "bob": (1100.0, 1800)}
    claims |= {"carol": (6000.0, 1800), "dave": (1500.0, 1800)}
    it = _folded(claims, ("alice", "bob", *order))
    assert _holders(it.lease_contest) == ["alice", "bob", "dave"]
    assert it.lease.holder == "carol"


@pytest.mark.parametrize("seed", range(40))
def test_the_contest_is_exactly_the_claims_with_an_overlap_partner(seed):
    """Property, against a brute-force oracle, in EVERY fold order of 3-5 random claims:
    no claim that overlapped another is ever missing from the contest, none that did not
    is in it, and `resolve --keep X` releases X's overlap partners and, if another holds
    the item, that holder -- nobody else. Be4ff1f82d3 and B6f34894078 both fail it."""
    rnd = random.Random(seed)
    names = ["a", "b", "c", "d", "e"][: rnd.choice([3, 4, 5])]
    claims = {w: (float(rnd.randrange(0, 40) * 50), rnd.choice([50, 100, 300, 800])) for w in names}
    partners = _partners(claims)
    want = sorted(w for w in names if partners[w])
    for order in permutations(names):
        it = _folded(claims, order)
        assert _holders(it.lease_contest) == want, (claims, order)
        for h in it.lease_contest:
            also = {it.lease.holder} - {h["holder"]}
            assert {g["holder"] for g in it.lease_losers(h)} == partners[h["holder"]] | also
        if it.lease_contest and it.lease.holder not in want:
            # The current holder met none of them: each ended before it took the item.
            assert _holders(it.lease_losers(_shown(it))) == want, (claims, order)


def test_show_names_who_each_contested_claim_overlapped(repo: Path):
    """FAILED before the fix: each claim was listed, but not whom it met."""
    from tests.conftest import run_cli

    log = EventLog(repo, "op")
    log.shard.parent.mkdir(parents=True, exist_ok=True)
    events = [
        _ev("task.added", "T", 1, title="t"),
        *(_acq(2 + i, who, *_REPRO[who]) for i, who in enumerate(_REPRO)),
    ]
    log.shard.write_text("".join(ev.to_json() + "\n" for ev in events))
    code, out, err = run_cli(repo, "show", "T", agent="op")
    assert code == 0, out + err
    lines = {ln.split(" by ")[1].split()[0]: ln for ln in out.splitlines() if "  claim " in ln}
    assert lines["carol"].endswith("overlapped alice")
    assert lines["bob"].endswith("overlapped alice")
    assert lines["alice"].endswith("overlapped bob and carol")

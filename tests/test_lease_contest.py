"""A lease contest keeps every claim that overlapped another, and says who met whom.

Bug Ba73ee6ea72. Alice claims at 1000 for 1000s; Bob, offline, at 1900 -- inside her
window. Carol claimed at 1200 for 100s: she overlapped Alice, and was over 600s before Bob
began. `show` said "claimed at once by alice and bob and carol", and `resolve --keep bob`
released Carol as having lost to a claim she never met.

The contest itself was right to hold all three: each of them overlapped SOMEBODY, and a
fold that drops a claim hides a real double claim (bug B6f34894078: the first fix did,
for chains of three). What was wrong is the reading of it. So the contest is the claims
that have an overlap partner, whatever order they fold in, and everything that reads it
works pairwise from the claims' own windows: the summary names who overlapped whom, and
`resolve --keep X` releases the claims that overlapped X -- plus the displayed lease if
it is another's, since X is about to take the item from it. Carol, kept out of that
release, leaves the contest when the resolution settles it: her one partner is gone.
"""

from __future__ import annotations

import random
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


def _folded(claims: dict[str, tuple[float, int]], order, *after: Event):
    """``claims`` (holder -> at, ttl), acquired in ``order`` -- fold order is Lamport, not
    wall time -- then ``after``."""
    return fold(
        [
            _ev("task.added", "T", 1, title="t"),
            *(_acq(2 + i, who, *claims[who]) for i, who in enumerate(order)),
            *after,
        ]
    ).items["T"]


def _holders(claims) -> list[str]:
    return sorted(h["holder"] for h in claims)


def _shown(it) -> dict:
    """The displayed lease as a claim record, the way `resolve` names it."""
    return next(c for c in it.lease_candidates() if c["event"] == it.lease.event)


def _partners(claims: dict[str, tuple[float, int]]) -> dict[str, set[str]]:
    """The oracle: who overlapped whom, brute force over the windows, both ends closed."""
    return {
        a: {b for b, (b0, bt) in claims.items() if b != a and a0 <= b0 + bt and b0 <= a0 + at}
        for a, (a0, at) in claims.items()
    }


_REPRO = {"alice": (1000.0, 1000), "bob": (1900.0, 1000), "carol": (1200.0, 100)}
_ORDERS = list(permutations(_REPRO))


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


_GROUPS = {"bob": (550.0, 300), "carol": (350.0, 300), "alice": (1750.0, 50), "dave": (1600.0, 800)}


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


# -- releases: a release takes one claim off the record, never the claims left ----------


def _run(
    claims: dict[str, tuple[float, int]],
    seq,
    holders: dict[str, str] | None = None,
    at: float = 0.0,
):
    """Fold ``seq`` of ("acq" | "rel", name): each claim acquired as ``holders[name]``
    (default: its own name), each release naming the claim's acquiring event. A
    ("res", n) step is `resolve` at that point, keeping the n-th contestant (mod the
    count) as `api.items.resolve` would -- ("hold", _) keeps the displayed lease, the
    current holder, whether or not it is a contestant: releases of `lease_losers`, then the
    resolution, dated ``at`` -- 0 by default, so the kept claim's window stays its own; a
    date after every claim makes a lapsed kept claim start a fresh window there
    (D-resolve-fresh-window). The claims those releases name are added to ``seq``'s
    ``gone`` attribute when it has one."""
    holders = holders or {}
    evs, ids = [_ev("task.added", "T", 1, title="t")], {}
    for kind, w in seq:
        k = len(evs) + 1
        who = holders.get(w, w)
        if kind == "acq":
            evs.append(
                _ev("lease.acquired", "T", k, who, holder=who, at=claims[w][0], ttl_s=claims[w][1])
            )
            ids[w] = evs[-1].id
        elif kind == "rel":
            evs.append(_ev("lease.released", "T", k, who, holder=who, event=ids[w]))
        else:
            it = fold(evs).items["T"]
            if not it.lease_contest:
                continue
            if kind == "hold":
                kept = _shown(it)
            else:
                kept = sorted(it.lease_contest, key=lambda h: h["holder"])[
                    w % len(it.lease_contest)
                ]
            for h in it.lease_losers(kept):
                evs.append(
                    _ev(
                        "lease.released",
                        "T",
                        len(evs) + 1,
                        "op",
                        holder=h["holder"],
                        event=h["event"],
                    )
                )
                getattr(seq, "gone", set()).add(h["holder"])
            data = {"kind": "task", "claim": kept, "at": at}
            evs.append(Event("item.resolved", "T", data, "op", len(evs) + 1))
    return fold(evs).items["T"]


class _Seq(list):
    """A step list that collects the claims its resolutions released."""

    gone: set[str]


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


_KEEP_FAR = {"X": (1000.0, 1000), "Y": (1900.0, 1000), "M": (150.0, 150), "K": (100.0, 100)}


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


# -- three pre-existing gaps: a holder's own re-claim, a long handover history, and a ---
# -- renewal by a contestant that is neither displayed nor displaced --------------------


def _contest(events: list[Event]) -> set[str]:
    """The acquiring events in the contest after folding ``events`` in Lamport order."""
    it = fold(sorted(events, key=Event.sort_key)).items["T"]
    return {h["event"] for h in it.lease_contest}


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


def _history(rnd: random.Random, renewals: bool = True):
    """A random multi-clone history: 2-4 holders, each with 1-4 claims of its own in
    sequence (a holder re-claims only after its previous claim lapsed), each claim with
    0-2 renewals inside its live window. Returns (claims, per-holder event lists): each
    claim is (holder, event, start, end) with end its last renewal plus TTL."""
    holders = ["a", "b", "c", "d"][: rnd.choice([2, 3, 4])]
    claims, streams, lamport = [], {}, 2
    for who in holders:
        t, stream = float(rnd.randrange(0, 20) * 50), []
        for _ in range(rnd.choice([1, 2, 3, 4])):
            ttl = rnd.choice([50, 100, 300, 800])
            acq = _acq(lamport, who, t, ttl)
            lamport += 1
            stream.append(acq)
            end = t + ttl
            for _ in range(rnd.choice([0, 0, 1, 2]) if renewals else 0):
                at = rnd.uniform(end - ttl, end)
                at = float(round(max(at, t)))
                if at + ttl > end:
                    stream.append(_ev("lease.renewed", "T", lamport, who, holder=who, at=at))
                    lamport += 1
                    end = at + ttl
            claims.append((who, acq.id, t, end))
            t = end + rnd.choice([1, 50, 400])
        streams[who] = stream
    return claims, streams


def _interleave(rnd: random.Random, streams: dict[str, list[Event]]) -> list[Event]:
    """One fold order: each holder's own events in its own order (one clone's Lamport
    clock), the holders interleaved at random; Lamport renumbered to that order."""
    left = {w: list(s) for w, s in streams.items()}
    out = [_ev("task.added", "T", 1, title="t")]
    while any(left.values()):
        who = rnd.choice([w for w, s in left.items() if s])
        ev = left[who].pop(0)
        out.append(Event(**{**ev.__dict__, "lamport": len(out) + 1}))
    return out


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


# -- the current holder can win a contest it was never in --------------------------------


_TAKEN_OVER = {"alice": (1000.0, 1800), "bob": (1500.0, 1800), "carol": (9000.0, 1800)}


@pytest.mark.parametrize("order", list(permutations(_TAKEN_OVER)), ids="-".join)
@pytest.mark.parametrize(
    ("keep", "released"),
    [("carol", ["alice", "bob"]), ("alice", ["bob", "carol"]), ("bob", ["alice", "carol"])],
)
def test_resolve_can_keep_the_holder_that_took_over_after_a_double_claim(
    repo: Path, order, keep, released
):
    """B-resolve-cannot-keep-holder, FAILED before the fix for carol: Alice and Bob
    claimed at once in two clones; long after both lapsed, Carol took the item over
    cleanly. She is not in the contest -- she overlapped nobody -- and `resolve --keep
    carol` was refused as naming no contestant: the only ways out took the item from
    the live holder and handed it to a lapsed one."""
    log = EventLog(repo, "op")
    log.shard.parent.mkdir(parents=True, exist_ok=True)
    events = [
        _ev("task.added", "T", 1, title="t"),
        *(_acq(2 + i, w, *_TAKEN_OVER[w]) for i, w in enumerate(order)),
    ]
    log.shard.write_text("".join(ev.to_json() + "\n" for ev in events))
    it = fold(log.read_all()).items["T"]
    assert _holders(it.lease_contest) == ["alice", "bob"] and it.lease.holder == "carol"
    from ddflow.api import items

    out = items.resolve(repo, "T", keep=keep, agent="op")
    assert out.exit == 0, out.reason
    assert sorted(out.data["released"]) == released
    it = fold(log.read_all()).items["T"]
    assert it.lease_contest == [] and it.lease.holder == keep
    # The only history left is the kept claim's own window: every claim it met was
    # released, and a lapsed claim holds the item from the resolution on.
    assert [(e["holder"], e["lease"]["acquired_at"]) for e in it.displaced] == [
        (keep, _TAKEN_OVER[keep][0])
    ]
    assert it.lease.acquired_at > _TAKEN_OVER["carol"][0], "a fresh window, not a stretch"
    assert not it.contest_summary()


def test_resolve_names_the_current_holder_among_the_choices(repo: Path):
    """A keep that names nobody lists what it may name -- the current holder too."""
    log = EventLog(repo, "op")
    log.shard.parent.mkdir(parents=True, exist_ok=True)
    events = [
        _ev("task.added", "T", 1, title="t"),
        *(_acq(2 + i, w, *_TAKEN_OVER[w]) for i, w in enumerate(_TAKEN_OVER)),
    ]
    log.shard.write_text("".join(ev.to_json() + "\n" for ev in events))
    from ddflow.api import items

    out = items.resolve(repo, "T", keep="zed", agent="op")
    assert out.exit == 1
    assert "carol" in out.reason and "alice" in out.reason and "bob" in out.reason


def test_keeping_the_current_holder_when_no_lease_contest_is_refused(repo: Path):
    """Nothing to settle: an item with a holder and no contest is not contested."""
    log = EventLog(repo, "op")
    log.shard.parent.mkdir(parents=True, exist_ok=True)
    events = [_ev("task.added", "T", 1, title="t"), _acq(2, "carol", 9000.0, 1800)]
    log.shard.write_text("".join(ev.to_json() + "\n" for ev in events))
    from ddflow.api import items

    assert items.resolve(repo, "T", keep="carol", agent="op").exit == 3


# -- a resolution keeping a LAPSED claim starts a fresh window (D-resolve-fresh-window) --

_GAP = {"A": (1150.0, 300), "E": (1200.0, 300), "C": (150.0, 150), "D": (250.0, 50)}


def _resolved(claims, order, keep: str, at: float, *after: Event):
    """``claims`` folded in ``order``, then `resolve --keep keep` dated ``at`` as the api
    writes it -- a release of each of the kept claim's losers, then the resolution --
    then ``after``."""
    it = _folded(claims, order)
    kept = next(h for h in it.lease_candidates() if h["holder"] == keep)
    rel = [
        _ev("lease.released", "T", 20 + i, "op", holder=h["holder"], event=h["event"])
        for i, h in enumerate(it.lease_losers(kept))
    ]
    res = Event("item.resolved", "T", {"kind": "task", "claim": kept, "at": at}, "op", 30)
    return _folded(claims, order, *rel, res, *after), kept, rel


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

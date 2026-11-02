"""Shared helpers for the lease-contest tests (split from tests/test_lease_contest.py, rule r-small-files)."""

from __future__ import annotations

import random
from itertools import permutations

from helpers import make_event as _ev

from ddflow.core.events import Event
from ddflow.core.model import fold


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


def _contest(events: list[Event]) -> set[str]:
    """The acquiring events in the contest after folding ``events`` in Lamport order."""
    it = fold(sorted(events, key=Event.sort_key)).items["T"]
    return {h["event"] for h in it.lease_contest}


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


_GROUPS = {"bob": (550.0, 300), "carol": (350.0, 300), "alice": (1750.0, 50), "dave": (1600.0, 800)}
_KEEP_FAR = {"X": (1000.0, 1000), "Y": (1900.0, 1000), "M": (150.0, 150), "K": (100.0, 100)}

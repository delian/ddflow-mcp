"""Who is in line for which files, and what blocks a claim: the reservation queue.

Part of `ddflow.api.lifecycle`, which re-exports every name defined here."""

from __future__ import annotations

import time
from pathlib import Path

from ...core import clock
from ...core.admission import glob_conflict
from ...core.model import ABANDONED, DONE, REVIEW
from ...core.schedule import (
    Blocked,
    capacities,
    find_cycles,
    is_external,
    item_blocker,
    resource_shortfall,
)
from ...services import leases as L
from ...services import waits as WT


def _reservation_hold(repo: Path, st, cfg, me: str, now: float | None = None):
    """The `hold` hook for `plan`: an item reserved for a waiter in line is not offered.

    `claim` would refuse it, so `next` must not offer it. `plan` applies this BEFORE it
    cuts the ready list to the free slots and checks offered items against each other, so
    the slot it frees goes to the next item and nothing is blocked on an item that is not
    in fact offered. None when reservations are off.
    """

    if cfg.lease.waiter_reservation_s <= 0:
        return None
    now = time.time() if now is None else now
    live = st.active_leases(now, cfg.lease.grace_s)

    def hold(i):
        ahead = _reserved_for(repo, st, cfg, i, me, list(i.globs), live, now)
        if ahead is None:
            return None
        return Blocked(i.id, "conflict", _reserved_msg(i.id, ahead, cfg), [ahead.item])

    return hold


#: Blocker reasons that clear on their own when another agent finishes. Everything else
#: -- a cycle, a contest, an operator's hold, a release line gone from config, an
#: umbrella -- needs someone to ACT, and waiting on it would idle until the deadline and
#: look like progress while doing so.
WAITABLE = frozenset({"conflict", "deps", "resources"})


def _clears_on_release(st, b, others, by_item=None, seen=None) -> bool:
    """Can blocker ``b`` clear when other agents let go, with nobody else acting?

    A file or item conflict, a resource shortfall and a full parallelism cap free on
    any release. A dependency clears when EVERY dependency still unmet will: one in
    motion (held, in review, external), or one whose own blocker -- looked up in
    ``by_item`` -- clears in turn. The rest -- a cycle, an expired lease under
    `reclaim_policy = "report"`, an operator's block, a contest, an umbrella -- need a
    person, as the single-item `wait` already says. A dependency with no blocker on
    record is not judged stuck: when in doubt, waiting is the old behaviour.
    """
    if b.reason in ("conflict", "resources"):
        return True
    if b.reason == "state":
        return "cap reached" in b.detail
    if b.reason != "deps" or not b.waiting_on:
        return False
    by_item = by_item or {}
    seen = (seen or set()) | {b.item}
    for d in b.waiting_on:
        if _in_motion(st, d, others):
            continue
        own = by_item.get(d)
        if own is None:
            continue
        if d in seen or not _clears_on_release(st, own, others, by_item, seen):
            return False
    return True


def _blocking_leases(st, blocked, others) -> list[str]:
    """The held items whose release could free something in ``blocked``.

    What the waiter registers against, and so which holders are told someone waits on
    them. Every lease in flight was the first answer, and it told an unrelated holder at
    each heartbeat that it was holding someone up. A full cap or a resource shortfall is
    freed by ANY release, and when nothing names a holder the answer stays "all of them":
    over-reporting a waiter is harmless, under-reporting hides one.
    """
    named: set[str] = set()
    for b in blocked:
        if b.reason == "resources" or (b.reason == "state" and "cap reached" in b.detail):
            return sorted(others)
        for w in b.waiting_on:
            if w in others:
                named.add(w)
            elif w in st.items:
                named.update(k.id for k in st.open_descendants(w) if k.id in others)
    return sorted(named) or sorted(others)


def _expired_blocker(cfg, it, me: str, live):
    """The `expired` verdict for an item whose holder's lease ran out, under
    `reclaim_policy = "report"`; None otherwise."""
    lz = it.lease
    if not (
        lz is not None
        and it.id not in live
        and lz.holder != me
        and cfg.lease.reclaim_policy == "report"
    ):
        return None
    # Expiry frees the holder's GLOBS for everyone else, but not its item: `claim`
    # refuses an expired lease, because a crashed agent's tree often holds finished
    # work. Calling it claimable made wait -> refused -> wait a spin -- for the item
    # path, and (critic) for an any-wait under deps_only, whose ready set is
    # filtered through here.
    return Blocked(
        it.id,
        "expired",
        f"{it.id}'s lease from {lz.holder} has expired and is not handed on "
        f"automatically. `ddflow recover --item {it.id}` says what its tree holds; "
        f"salvage, release it, then claim.",
        [],
    )


def _resource_blocker(cfg, it, live):
    """The `resources` verdict for an item whose declared resources are all in use."""
    if not it.resources or it.id in live:
        return None
    try:
        short = resource_shortfall(it.resources, live, capacities(cfg), exclude=it.id)
    except ValueError:
        short = ""  # a declaration error: claim reports it, and waiting cannot fix it
    return Blocked(it.id, "resources", short, []) if short else None


def _claim_blocker(
    st,
    cfg,
    it,
    me: str,
    live,
    now: float,
    *,
    globs: list[str] | None = None,
    repo: Path | None = None,
    fair: bool = True,
):
    """Why `claim` would refuse ``it`` for ``me`` right now, as a `Blocked`; None if not.

    The scheduler's predicate plus the checks `claim` applies unconditionally -- an
    expired lease under `reclaim_policy = "report"`, another holder's live lease on the
    item, overlapping globs, resource capacity.
    `item_blocker` skips these under `ready_policy = deps_only`, and waking on a
    verdict `claim` then refuses would spin (roborev: it did, for a held item).
    """
    cycles = find_cycles({i.id: i for i in st.items.values() if not i.removed})
    in_cycle = {n for c in cycles for n in c}
    b = item_blocker(st, cfg, it, live, agent=me, now=now, in_cycle=in_cycle, cycles=cycles)
    if b is not None:
        return b
    expired = _expired_blocker(cfg, it, me, live)
    if expired is not None:
        return expired
    held = live.get(it.id)
    if held is not None and held.holder != me:
        return Blocked(it.id, "conflict", f"leased by {held.holder}", [it.id])
    # The globs the claim will name, when the caller says (`wait --globs`): judged on the
    # stored ones, READY was followed by a claim refused for its own globs (B7036cf788c).
    clash = L.glob_clash(st, cfg, it, me, list(it.globs if globs is None else globs), now)
    if clash is not None:
        other_id, lz, pair = clash
        return Blocked(
            it.id,
            "conflict",
            f"globs overlap {other_id} held by {lz.holder} ({pair[0]} vs {pair[1]})",
            [other_id],
        )
    short = _resource_blocker(cfg, it, live)
    if short is not None:
        return short
    if fair and repo is not None:
        # Last: the files are free, and the question is only whether someone is in line.
        want = list(it.globs if globs is None else globs)
        w = _reserved_for(repo, st, cfg, it, me, want, live, now)
        if w is not None:
            return Blocked(it.id, "conflict", _reserved_msg(it.id, w, cfg), [w.item])
    return None


def _fmt_since(t: float) -> str:
    """The UTC time of day, `01:02:03Z` (core.clock, looked up at call time)."""
    return clock.fmt_time(t)


def _reserved_msg(item: str, w, cfg) -> str:
    return (
        f"{item} is reserved for {w.agent} (waiting since {_fmt_since(w.since)} for "
        f"{w.item}, which needs the same files); their place is held for "
        f"{cfg.lease.waiter_reservation_s}s after they could claim "
        f"([lease].waiter_reservation_s)"
    )


def _reserved_for(repo: Path, st, cfg, it, me: str, globs: list[str], live, now: float):
    """The live waiter that stands ahead of ``me`` for files ``it`` would take, or None.

    First come, first served on a contended file. Without it, a claim freed a file for
    whoever polled first, and a hot one starved its longest waiter for hours. A waiter
    reserves only while its own claim would succeed (it is not still behind someone
    else, and has not lapsed: see `Waiter.live`), only against a claim of overlapping
    files, and only when it is older than the claimant -- a strict order, so it cannot
    deadlock, and two waiters on disjoint files never see each other.
    """

    if cfg.lease.waiter_reservation_s <= 0:
        return None
    try:
        waiters = WT.live_waiters(repo, now)
    except (OSError, ValueError, TypeError):
        return None  # advisory: an unreadable registry is no queue
    mine = next(((w.since, w.agent) for w in waiters if w.agent == me and w.item == it.id), None)
    for w in waiters:  # oldest first
        if (
            w.agent == me
            or not w.item
            or not w.reserves()
            or (mine is not None and mine <= (w.since, w.agent))
        ):
            continue
        target = st.items.get(w.item)
        if target is None or target.removed or target.state in (DONE, ABANDONED, REVIEW):
            continue
        if w.item in live:
            continue  # someone holds its item: the lease and glob checks speak for it
        if w.item != it.id and not glob_conflict(
            st, cfg, it, globs, against="offered", others=[target]
        ):
            continue  # no overlap, or a different release line (a different branch)
        # Reservation-aware too: a waiter held back by an even older one reserves nothing.
        # Terminates: asked as `w.agent`, only waiters OLDER than `w` stand ahead of it.
        if _claim_blocker(st, cfg, target, w.agent, live, now, repo=repo) is not None:
            continue  # still behind another holder: nothing to keep free for it yet
        return w
    return None


def _in_motion(st, dep: str, others: dict) -> bool:
    """Will `dep` finish without the caller? Held by another agent, under review, in
    another repository, or with a sub-task someone holds."""

    if is_external(dep) or dep in others:
        return True
    d = st.items.get(dep)
    if d is None:
        return False
    if d.state == REVIEW:
        return True
    return any(k.id in others for k in st.open_descendants(dep))

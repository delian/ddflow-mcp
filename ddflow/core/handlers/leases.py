"""Fold handlers: leases and the claim contest: acquired, renewed, released/expired, resolved.

The `_h_*` functions are fold handlers (`(State, Event) -> None`; `_h_added`/`_updated`/
`_removed` also take the record kind) or handler factories (`_h_gate`, `_h_state`), assembled
into `model.HANDLERS`; the other functions are the helpers they share. All of it is pure."""

from __future__ import annotations

from typing import Any

from ..events import Event
from ..records import (
    DEFAULT_LEASE_TTL_S,
    OPEN,
    RUNNING,
    Item,
    Lease,
    State,
    _claim,
    _clashing,
    _overlaps,
)
from ._common import _item
from .items import _apply_definition


def _key(claim: dict[str, Any]) -> tuple[str, float]:
    """One WINDOW of a claim. A claim is its acquiring event -- except after `resolve`
    kept it once it had lapsed: then it holds the item in a fresh window from the
    resolution while its original window stays on the record (D-resolve-fresh-window),
    and the two must not be merged, widened or deduplicated into each other."""
    return claim["event"], claim["lease"]["acquired_at"]


def _join(it: Item, claims: list[dict[str, Any]]) -> None:
    """Add claims to the lease contest, once each. Keyed by the claim WINDOW (`_key`: the
    acquiring event, and the window's start), never by holder name: one holder can hold
    an item twice (claimed, lost, claimed again), and those are two claims that can each
    be contested; one claim kept by a resolution after it lapsed has two windows. A claim already there keeps the
    later evidence -- a renewal since it joined widens the window everything pairwise
    reads."""
    have = {_key(h): h for h in it.lease_contest}
    for c in claims:
        old = have.get(_key(c))
        if old is None:
            it.lease_contest.append(c)
            have[_key(c)] = c
            continue
        old["lease"]["renewed_at"] = max(old["lease"]["renewed_at"], c["lease"]["renewed_at"])
        if "overlapped_by" in c:
            old.setdefault("overlapped_by", c["overlapped_by"])


def _displace(it: Item, lost: dict[str, Any], by: dict[str, Any]) -> None:
    """Put ``lost`` on the record as history ``by`` replaced. Never capped: a clone may
    deliver a claim or a renewal from any point in the past, and a claim forgotten here
    is a double claim nobody is told about (B7164b23643: a cap of eight lost one after
    the ninth clean handover)."""
    if any(_key(e) == _key(lost) for e in it.displaced):
        return
    it.displaced = [*it.displaced, {**lost, "by": by}]


def _hold(it: Item, lease: Lease) -> None:
    """Make ``lease`` the displayed one, and point the item at its tree."""
    it.lease = lease
    it.worktree = lease.worktree or it.worktree
    it.branch = lease.branch or it.branch


def _h_lease_acquired(st: State, ev: Event) -> None:
    """A claim, weighed against every claim it could have collided with.

    `acquire` never grants a claim over another holder's live one in one log -- a live
    lease is refused even with --force, and a takeover needs a release, a recorded
    expiry, or the lease past its TTL (and grace) at the moment of the claim. So two
    claims whose intervals intersect can only come from clones that did not see each
    other: a contest. The comparison is on the claims' own times, never on "now" (the
    fold is pure), and against EVERY claim the item still knows -- the displayed lease,
    each one already contested, each one a takeover displaced -- so the contest is the
    claims that overlapped another, whatever order they fold in. It is never narrowed to
    the claims that met the displayed one: that hid real double claims. Who met whom is
    read pairwise from it (`Item.lease_clashes`).

    Which claim is DISPLAYED is decided by wall time, not fold order: the later
    acquisition. A claim that ended before the displayed one began is history arriving
    late; it is remembered as displaced (a late renewal may yet prove it overlapped) and
    never replaces the lease.
    """
    d = ev.data
    it = _item(st, ev, d.get("kind", "task"))
    new = Lease(
        holder=d.get("holder", ev.agent),
        acquired_at=float(d.get("at", 0.0)),
        renewed_at=float(d.get("at", 0.0)),
        ttl_s=int(d.get("ttl_s", DEFAULT_LEASE_TTL_S)),
        worktree=d.get("worktree", ""),
        branch=d.get("branch", ""),
        globs=list(d.get("globs", [])),
        note=d.get("note", ""),
        resources=list(d.get("resources", [])),
        event=ev.id or ev.compute_id(),
        prior_state=it.state,
        prior_holder=it.lease.holder if it.lease else "",
    )
    mine = _claim(new)
    cur = it.lease
    rivals = _clashing(_known(it), mine)
    if rivals:
        _join(it, [*rivals, mine])
    if cur is None or cur.event == mine["event"]:
        _hold(it, new)
        return
    held = _claim(cur)
    if cur.holder == new.holder or cur.expired_at:
        # Not a rival's live claim: the holder's own earlier claim, re-claimed after it
        # lapsed, or one whose expiry is recorded. The new claim is displayed, as it
        # always was -- but the old one stays on the record: a claim from another clone
        # that overlapped IT, folding later, is still a double claim (B6ac30c2acb).
        _displace(it, held, mine)
        _hold(it, new)
        return
    if _overlaps(held["lease"], mine["lease"]):
        if new.acquired_at >= cur.acquired_at:
            _hold(it, new)
        return
    if new.acquired_at < cur.acquired_at:
        _displace(it, mine, held)  # late history: `cur` took over from it
        return
    _displace(it, held, mine)
    _hold(it, new)


def _known(it: Item) -> list[dict[str, Any]]:
    """Every claim the item still knows: each contested, each displaced, and the
    displayed lease. A claim can sit in more than one list; `_clashing` skips its own
    event and `_join` keys by window (`_key`), so a duplicate changes nothing."""
    return [
        *it.lease_contest,
        *({k: v for k, v in e.items() if k != "by"} for e in it.displaced),
        *([_claim(it.lease)] if it.lease is not None else []),
    ]


def _widen(it: Item, key: tuple[str, float], at: float) -> None:
    """A renewal at ``at`` of the claim window ``key`` (`_key`): every record of that
    window -- contested or displaced -- now runs to at least ``at`` plus its TTL. Never
    another window of the same claim: renewing the fresh window a resolution started
    must not stretch the original back over the gap (D-resolve-fresh-window)."""
    for h in [*it.lease_contest, *it.displaced]:
        if _key(h) == key:
            h["lease"]["renewed_at"] = max(h["lease"]["renewed_at"], at)


def _late_renewal(it: Item, d: dict[str, Any]) -> None:
    """A renewal of a claim that is not the displayed lease, folded after something
    replaced it -- a TTL takeover, or another clone's claim that won the display.

    One log cannot produce it -- `renew` refuses anyone but the current holder -- so it
    came from a clone that never saw what replaced the claim. It belongs to the holder's
    LATEST claim acquired at or before the renewal's own time, contested or displaced: a
    holder's claims follow one another in its own clone, so that is the one it was
    renewing. The renewal widens that claim's window, and the widened claim is weighed
    pairwise against every claim the item knows, as a new claim would be: each it now
    overlapped joins it in the contest. Nothing that overlapped is dropped -- not a
    contestant's renewal (Bee8e21c547), not one many takeovers back.
    """
    if "at" not in d:
        return
    at = float(d["at"])
    shown = _key(_claim(it.lease)) if it.lease is not None else None
    own = [
        c
        for c in [*it.lease_contest, *it.displaced]
        if c["holder"] == d["holder"] and _key(c) != shown and c["lease"]["acquired_at"] <= at
    ]
    if not own:
        return
    hit = max(own, key=lambda c: c["lease"]["acquired_at"])
    _widen(it, _key(hit), at)
    claim = {k: v for k, v in hit.items() if k != "by"}
    rivals = _clashing(_known(it), claim)
    if not rivals:
        return
    by = next((e["by"] for e in it.displaced if _key(e) == _key(hit)), None)
    if by is not None and any(r["event"] == by["event"] for r in rivals):
        claim["overlapped_by"] = by["holder"]
    # Only what it overlapped: the lease displayed now is not joined unless it is one of
    # them -- `resolve` can keep the current holder without it being a contestant (bug
    # B-late-renewal-overjoin, which joined it overlap or not).
    it.displaced = [e for e in it.displaced if _key(e) != _key(hit)]
    _join(it, [claim, *rivals])


def _h_lease_renewed(st: State, ev: Event) -> None:
    it = st.items.get(ev.subject)
    if not it:
        return
    d = ev.data
    # Only the CURRENT claim may be renewed. Lamport values are computed independently on
    # unsynced clones, so merging two shards can order a former holder's renewal after
    # a later re-acquisition by someone else -- and applying it would point the live
    # lease at the dead agent's worktree, which every destructive path then targets. A
    # renewal stamped before the displayed claim was acquired is not of it either, even
    # from the same holder: it renewed an EARLIER claim of theirs.
    stale = it.lease is not None and "at" in d and float(d["at"]) < it.lease.acquired_at
    if "holder" in d and (it.lease is None or d["holder"] != it.lease.holder or stale):
        _late_renewal(it, d)
        return
    if not it.lease:
        return
    # A renewal may only move the clock FORWARD. A stale renewal reordered after a
    # re-acquisition would otherwise set `renewed_at` back to its own older timestamp,
    # and a live lease would read as expired -- inviting another agent to take an item
    # someone is actively editing.
    at = float(d.get("at", it.lease.renewed_at))
    if at < it.lease.renewed_at:
        return
    it.lease.renewed_at = at
    # The displayed claim may be contested or on the record too: its window widens
    # there as well, and whatever it now overlapped joins it.
    _widen(it, _key(_claim(it.lease)), at)
    rivals = _clashing(_known(it), _claim(it.lease))
    if rivals:
        _join(it, [_claim(it.lease), *rivals])
    # Absent keys leave the field alone; only a present key updates, so a plain
    # heartbeat never clears an attachment.
    if "worktree" in d:
        it.lease.worktree = d["worktree"]
        it.worktree = d["worktree"] or it.worktree
    if "branch" in d:
        it.lease.branch = d["branch"]
        it.branch = d["branch"] or it.branch
    if "globs" in d:
        it.lease.globs = list(d["globs"])
    if "resources" in d:
        it.lease.resources = list(d["resources"])


def _released_claim(claims: list[dict[str, Any]], d: dict[str, Any]) -> str:
    """The acquiring event a release names, among ``claims``; "" when none.

    By `event` when the release carries it (every release written since B191 does). An
    older release names only a holder, and then it is taken to end that holder's LATEST
    claim -- the one it held when it released.
    """
    if d.get("event"):
        return d["event"] if any(c["event"] == d["event"] for c in claims) else ""
    own = [c for c in claims if c["holder"] == d.get("holder")]
    return max(own, key=lambda c: c["lease"]["acquired_at"])["event"] if own else ""


def _h_lease_gone(st: State, ev: Event) -> None:
    """Handle `lease.released` and `lease.expired`.

    Two rules, both learned from a cross-family review that probed reordered shards:

    1. **A release only ends the lease it names.** Shard merges can order a former
       holder's release AFTER a newer acquisition, and unconditionally clearing the
       lease then destroys the CURRENT holder's claim — another agent can take the item
       while the first is mid-edit. This is the same class as the stale-renewal bug
       fixed earlier; that fix patched one handler and left its siblings, which is
       exactly the incomplete-fix failure the rule against it describes.
    2. **Expiry keeps the lease object, marked expired.** Deleting it loses the
       worktree pointer, so `State.expired_leases()` could never report an expiry and a
       cleanup pass sees an item with no lease at all. Keeping it with `ttl_s = 0` makes
       `expired()` true, so it leaves `active_leases` and appears in `expired_leases`
       with its worktree intact — which is what recovery needs to protect the tree.
    """
    it = st.items.get(ev.subject)
    if not it:
        return
    d = ev.data
    holder = d.get("holder")
    if ev.kind == "lease.released" and holder is not None:
        # A claim given up can no longer contradict a takeover of it.
        gone = _released_claim(it.displaced, d)
        it.displaced = [e for e in it.displaced if e["event"] != gone]
        gone = _released_claim(it.lease_contest, d)
        if gone:
            _withdraw_claim(it, gone)
            return
    elif ev.kind == "lease.expired" and holder is not None:
        # A contestant whose expiry is RECORDED is no longer live, so it cannot collide
        # with a claim that comes after -- its snapshot has to say so.
        gone = _released_claim(it.lease_contest, d)
        for h in it.lease_contest:
            if h["event"] == gone:
                h["lease"].update(ttl_s=0, expired_at=ev.ts)
    if not it.lease:
        return
    if holder is not None and holder != it.lease.holder:
        return
    if d.get("event") and d["event"] != it.lease.event:
        return
    if ev.kind == "lease.expired":
        it.lease.ttl_s = 0
        it.lease.expired_at = ev.ts
        return
    gone_lease = it.lease
    it.lease = None
    # A deliberate release hands the item back: RUNNING with nobody on it is what a
    # crash looks like, and a release is not a crash (B601fa7eff9). Expiry keeps RUNNING
    # (above) so recovery still sees it; a transfer (re-homing) is re-acquired at once;
    # and a lease taken on an item that was ALREADY running (a crashed one, then a
    # refused or abandoned takeover) gives it back running, crash signal intact.
    # Kept RUNNING only for a lease that never took the item on (a refused or undone
    # takeover) of ANOTHER holder's crashed item. A re-homing has no prior holder; a
    # takeover that started work and is released deliberately hands the item back.
    others_crash = (
        gone_lease.prior_state == RUNNING
        and bool(gone_lease.prior_holder)
        and gone_lease.prior_holder != gone_lease.holder
        and not gone_lease.started
    )
    if it.state == RUNNING and not d.get("transfer") and not others_crash:
        it.state = OPEN
    _redisplay(it)


def _redisplay(it: Item) -> None:
    """The displayed lease was released while a contest stands: the latest contestant is
    displayed -- the fold's own rule, and what the log without the released claim shows.
    A displaced claim is never promoted: it lapsed before a takeover, and reviving it
    would turn the next ordinary claim into a recovery."""
    if it.lease is None and it.lease_contest:
        _hold(it, Lease(**max(it.lease_contest, key=lambda h: h["lease"]["acquired_at"])["lease"]))


def _withdraw_claim(it: Item, event: str) -> None:
    """A contested claim was released: it is withdrawn -- it, and nothing else. A claim
    left with no overlap partner has nothing to resolve: it leaves the contest (all of
    them do, once no two overlapped) and goes back on the record as history the
    displayed lease displaced, where a later claim is still weighed against it."""
    it.lease_contest = [h for h in it.lease_contest if h["event"] != event]
    if it.lease is not None and it.lease.event == event:
        it.lease = None
    _redisplay(it)
    alone = [h for h in it.lease_contest if not _clashing(it.lease_contest, h)]
    if not alone or it.lease is None:
        return
    held, kept = _claim(it.lease), {e["event"] for e in it.displaced}
    for h in alone:
        if h["event"] not in (held["event"], *kept):
            _displace(it, h, held)
    it.lease_contest = [h for h in it.lease_contest if h not in alone]


def _h_resolved(st: State, ev: Event) -> None:
    """An operator settled a contest: the kept definition and/or claim is re-applied.

    The payload CARRIES what was kept rather than pointing at it, so the fold needs no
    second pass over the log, and the record says what was decided in its own words.
    """
    it = st.items.get(ev.subject)
    if it is None:
        return
    d = ev.data
    keep = d.get("definition")
    if keep:
        _apply_definition(st, it, keep.get("data", {}), d.get("kind", it.kind))
        st.definitions[it.id] = keep
        it.contested = []
    claim = d.get("claim")
    if claim:
        lease = Lease(**claim["lease"])
        at = float(d.get("at", 0.0))
        # The operator's decision is itself a sign of life: the kept holder's TTL runs
        # from here, not from a claim that may be hours old. But a claim that had LAPSED
        # by then was not held in between, and stretching it over the gap made every
        # claim taken there -- a clean takeover -- sit inside it (B6805b48aca). It holds
        # the item in a fresh window from the resolution, and its own window stays on
        # the record, where a claim that met its real tenure is still weighed against
        # it (D-resolve-fresh-window).
        lapsed = lease.renewed_at + lease.ttl_s < at
        if lapsed:
            lease.acquired_at = lease.renewed_at = at
            # A recorded expiry zeroed the claim's TTL and marked it expired: the fresh
            # window runs on the configured TTL the resolution carries (roborev).
            lease.expired_at = ""
            if lease.ttl_s <= 0:
                lease.ttl_s = int(d.get("ttl_s") or DEFAULT_LEASE_TTL_S)
        else:
            lease.renewed_at = max(lease.renewed_at, at)
        # What the resolution did not release stays on the record: claims that still
        # overlap each other are still a contest -- this one settled the kept claim's,
        # not theirs -- and a claim with no partner left is history the kept claim
        # displaced, which a later claim is still weighed against.
        rest = [h for h in it.lease_contest if h["event"] != claim["event"]]
        standing = [h for h in rest if _clashing(rest, h)]
        on_record = {claim["event"], *(h["event"] for h in standing)}
        on_record |= {e["event"] for e in it.displaced}
        for h in [*rest, *([_claim(it.lease)] if it.lease is not None else [])]:
            if h["event"] not in on_record:
                _displace(it, h, claim)
                on_record.add(h["event"])
        it.displaced = [e for e in it.displaced if e["event"] != claim["event"]]
        _hold(it, lease)
        it.lease_contest = standing
        held = _claim(lease)
        if lapsed:
            _displace(it, {k: v for k, v in claim.items() if k != "by"}, held)
        # Only what the window it now holds overlaps is weighed again: for a fresh
        # window, the claims taken in the gap met nobody and stay out of it.
        rivals = _clashing(_known(it), held)
        if rivals:
            _join(it, [held, *rivals])

"""Renew, release, expire, retarget and transfer a claim; the foreign-clone guard.

``_transition`` is the shared read-then-write body ``renew``, ``release`` and
``expire`` were ~85% of; the retarget pair handles ``update --globs``/``--resources``
on a claimed item.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import Any

from ...config import Config
from ...core.model import Item, Lease, State
from ...core.schedule import capacities, resource_shortfall
from ...infra.log import EventLog
from .. import leases as _leases
from .. import waiting as WG
from ..leaseclock import foreign
from ..logcoalesce import quiet_renewal, renew_window
from .common import _clash_text, _clone_field, _fold, glob_clash
from .model import LeaseError
from .remote import _remote_drop, _remote_renew, _remote_take


def _transition(
    log: EventLog,
    item_id: str,
    kind: str,
    *,
    mine: bool,
    holder: str,
    payload: Callable[[Lease], dict[str, Any]],
    refuse: Callable[[State, Item], bool] | None = None,
    quiet: Callable[[Lease], bool] | None = None,
) -> bool:
    """Append one lease-lifecycle event, under the lock, if the lease is in a fit state.

    `renew`, `release` and `expire` were ~85% one body: open a transaction, fold, find
    the item, check it has a lease, append. They differed in a guard (renew requires the
    lease to be MINE; the other two do not) and in a payload. Three copies of a
    read-then-write under a lock is three chances for one of them to drift out of the
    lock — and the copies had already drifted in whether they recorded the holder.

    The read which decides and the write which acts must not be separated by another
    agent's append. That is held by `EventLog.decide_then_append` rather than by folding
    inside the lock: the fold happens outside, and under the lock the log is PROVED not to
    have changed. Where it has, it is re-folded there, which is the old behaviour exactly.
    """
    holder = holder or log.agent_id
    with log.decide_then_append(lambda: _fold(log)) as state:
        it = state.items.get(item_id)
        if not it or not it.lease:
            return False
        if mine and it.lease.holder != holder:
            return False
        if refuse is not None and refuse(state, it):
            return False
        if quiet is not None and quiet(it.lease):
            return True  # held and renewed, but not worth an event (`logcoalesce`)
        log.append(kind, item_id, payload(it.lease))
        return True


def renew(log: EventLog, item_id: str, holder: str = "") -> bool:
    """Extend MY lease. Refuses on someone else's: a renewal is a claim of possession."""
    holder = holder or log.agent_id

    def lapsed_onto_a_live_claim(state: State, it: Item) -> bool:
        # Reviving a LAPSED lease is a re-claim: expiry freed its files, and a live lease
        # granted on them since stands, as it does against `claim` (B0cb404c94e). Judged
        # on the globs the lease held: paths the item gained while it lapsed are the
        # heartbeat catch-up's, which keeps the lease on its held globs and says why
        # when any new one is taken (`globs_withheld`).
        now = _leases.time.time()
        lease = it.lease
        if lease is None or lease.live(now):
            return False  # live even before any grace: no config to read
        cfg = Config.load(log.root)
        if lease.live(now, cfg.lease.grace_s):
            return False
        return glob_clash(state, cfg, it, holder, list(lease.globs), now) is not None

    ok = _transition(
        log,
        item_id,
        "lease.renewed",
        mine=True,
        holder=holder,
        payload=lambda _lease: {"at": _leases.time.time(), "holder": holder},
        refuse=lapsed_onto_a_live_claim,
        quiet=lambda lease: quiet_renewal(
            lease,
            _leases.time.time(),
            renew_window(Config.load(log.root)),
            log.clone_id(create=False),
        ),
    )
    if ok:
        # A claim someone else took over on the remote is not renewed: False, as for any
        # lease we no longer hold.
        return not _remote_renew(log, Config.load(log.root), item_id, holder, _leases.time.time())
    return ok


def _clash_rule(
    item_id: str,
    it: Item,
    other_id: str,
    text: str,
    keep: list[str],
    keep_partial: bool,
) -> tuple[list[str] | None, str]:
    """The accepted globs and a refusal ("") for a widening that clashes with a live lease.

    B-ddflow-glob-widen-refused-broad: a widening that only PARTLY overlaps another
    agent's live lease takes the non-overlapping subset instead of refusing the whole
    list. Only a WIDENING applies: the accepted subset must retain every glob the item
    already holds, or the request still refuses (a narrow that drops a held glob, and a
    repeat ask of the same held globs, are not widenings and stay refusals).
    """
    if keep and keep_partial and set(keep) > set(it.globs):
        return keep, ""
    return None, (
        f"{item_id} is claimed, so its globs are its lease: {text}. The globs were not "
        f"changed; your lease stays as it is and the request is recorded as waiting. "
        f"Do not poll. Try narrower globs: drop the overlapping ones"
        f"{' (' + ','.join(keep) + ' would be accepted)' if keep else ''}, "
        f"name the exact files {other_id}'s holder does not change, or ask again when "
        f"{other_id} is done."
    )


def plan_retarget(
    log: EventLog,
    cfg: Config,
    item_id: str,
    globs: list[str] | None,
    resources: list[str] | None = None,
    *,
    now: float | None = None,
    keep_partial: bool = False,
) -> tuple[Lease | None, str, list[str] | None]:
    """(the LIVE lease to point at ``accepted`` globs / resources or None, why that is refused
    or "", and the globs actually to write -- the full ``globs`` when all were accepted, their
    non-overlapping subset when some were withheld, or None when ``globs`` is None).

    ``keep_partial`` (default off) lets a widening that only PARTLY overlaps another agent's
    live lease take the non-overlapping subset instead of refusing the whole list
    (B-ddflow-glob-widen-refused-broad). The default stays all-or-nothing for ``batch`` and
    the heartbeat catch-up, whose contracts promise that a refusal records nothing.

    ``now`` (default: the clock) is the instant the lease is judged live at: the heartbeat
    that revived a lapsed lease passes the instant it renewed it, so a slow process cannot
    find the lease it just revived lapsed again (B8eb33fe297).

    Call with ``log.transaction()`` held, and append nothing if the reason is non-empty.
    ``None`` leaves that field of the claim alone.

    `update --globs` on a claimed item changed `item.globs` only, while the commit hook
    and every conflict check read `lease.globs` -- so the remedy the hook prints for an
    uncovered path did nothing (B3eda99e0fe / B5d98a4da0a). Once an update reaches the
    lease it is a way to take paths, so it gets `claim`'s overlap check (`glob_clash`):
    widening must not take what another agent's live lease covers. Resources are the
    same gap (Bc496508f6b): the capacity check reads `lease.resources`, so a raised
    reservation gets `claim`'s capacity check, counted against every OTHER live lease.

    A released lease has nothing to retarget, and an expired one -- marked, or past its
    TTL and grace -- is left for recovery rather than touched, so no lease is brought
    back by an edit. An unclaimed item's globs are not checked, as before.
    """
    now = _leases.time.time() if now is None else now
    state = _fold(log)
    it = state.items.get(item_id)
    lz = it.lease if it else None
    if it is None or lz is None or not lz.live(now, cfg.lease.grace_s):
        return None, "", None
    accepted = globs
    clash = _clash_text(log, cfg, state, it, globs, now) if globs is not None else None
    if clash:
        other_id, text, keep = clash
        accepted, refusal = _clash_rule(item_id, it, other_id, text, keep, keep_partial)
        if refusal:
            return lz, refusal, None
    if globs is not None and accepted:
        # A glob this item ALREADY HOLDS is not a new demand: re-stating it in `update`
        # must not be judged as queued behind an earlier waiter (B-update-globs-queued-
        # refusal-vague). Only the NEW globs contend, and the refusal names which one.
        mine_glob = set(lz.globs) if lz else set()
        want = [g for g in accepted if g not in mine_glob]
        ahead = WG.queue_ahead(state, cfg, item_id, lz.holder, want, now)
        if ahead is not None:
            pairs = WG.conflicts(want, ahead.globs, WG.shared_globs(cfg))
            named = pairs[0][0] if pairs else (ahead.globs[0] if ahead.globs else "the requested files")
            reason = f"{named} is queued behind {ahead.holder} ({ahead.item}), who asked for it first."
            return (lz, f"{item_id}: {reason} The globs were not changed.", None)
    if resources:
        try:
            short = resource_shortfall(
                list(resources),
                state.active_leases(now, cfg.lease.grace_s),
                capacities(cfg),
                exclude=item_id,
            )
        except ValueError as exc:
            return lz, f"{item_id}: {exc}. Nothing was recorded.", None
        if short:
            return (
                lz,
                (
                    f"{item_id} is claimed, so its resources are its lease's reservation: "
                    f"{item_id} {short}. Nothing was recorded. Wait for one to be released, "
                    f"or ask for less."
                ),
                None,
            )
    return lz, "", accepted


def retarget(
    log: EventLog,
    item_id: str,
    lease: Lease,
    globs: list[str] | None,
    resources: list[str] | None = None,
) -> None:
    """Point ``lease`` (from `plan_retarget`, same lock) at ``globs`` / ``resources``.

    An explicit `lease.renewed`, rather than a fold that copies `task.updated` fields into
    the lease: the log says when the claim changed shape, old logs replay exactly as they
    were recorded, and the handler's holder guard drops it if shards reorder it after
    someone else's re-acquisition. It must never be a renewal in disguise: the payload
    names the CURRENT holder (an operator may widen a stuck agent's claim; it stays that
    agent's) and carries no ``at``, so ``renewed_at`` stays put -- the lease's life is
    not extended, and a heartbeat landing first cannot make it look stale. A field left
    ``None`` is left out of the payload, and the fold leaves it alone.
    """
    data: dict[str, object] = {"holder": lease.holder}
    if globs is not None:
        data["globs"] = list(globs)
    if resources is not None:
        data["resources"] = list(resources)
    log.append("lease.renewed", item_id, data)


def _released(
    holder: str,
    event: str,
    by: str,
    note: str = "",
    transfer: bool = False,
    forced: bool = False,
    lost_to: str = "",
) -> dict[str, Any]:
    """The one shape of a `lease.released` payload. ``event`` names WHICH claim ends: one
    holder can have held the item twice, and a fold that merges clones must not end the
    wrong one (B191)."""
    data: dict[str, Any] = {"holder": holder, "event": event, "by": by, "note": note}
    if transfer:
        data["transfer"] = True
    if forced:
        data["forced"] = True  # additive (D-compat): an older reader ignores the key
    if lost_to:
        # B2809f4213b, additive too: this claim LOST a contest to ``lost_to``, which is
        # what lets the loser's own clone say so (`lost_contests`).
        data["lost_contest"] = True
        data["winner"] = lost_to
    return data


def release_claim(
    log: EventLog,
    item_id: str,
    *,
    holder: str,
    event: str,
    by: str = "",
    note: str = "",
    transfer: bool = False,
    lost_to: str = "",
) -> None:
    """Append the `lease.released` that ends ONE named claim (``holder``'s, granted by
    ``event``). Used where the claim is named rather than looked up: a lost contest ends a
    displaced claim (`resolve`), and a transfer ends the claim it hands on.
    The caller holds ``log.transaction()`` and settles the remote claim ref itself
    (`settle_remote`)."""
    log.append(
        "lease.released",
        item_id,
        _released(holder, event, by or log.agent_id, note, transfer, lost_to=lost_to),
    )


def settle_remote(
    log: EventLog, cfg: Config, item_id: str, *, dropped: list[str], kept: str = ""
) -> None:
    """After a claim moved: give back the remote claim ref of each ``dropped`` holder,
    then (when ``kept``) take it for the holder that keeps the item. Outside the log
    lock, best effort -- a ref not moved lapses at its expiry. No-op unless
    `[flow].claims = "remote"`."""
    if cfg.flow.claims != "remote":
        return
    for holder in dropped:
        _remote_drop(log, item_id, holder)
    if kept:
        with contextlib.suppress(LeaseError):
            _remote_take(log, cfg, item_id, kept, _leases.time.time())


def transfer(
    log: EventLog, item_id: str, lease: Lease, *, to: str, note: str, kind: str, now: float
) -> None:
    """Hand ``lease`` to ``to`` unchanged: release it as a transfer, then acquire it again
    under the new holder with the same tree, branch, globs, resources and TTL. The caller
    holds ``log.transaction()`` and calls `settle_remote` afterwards."""
    release_claim(
        log, item_id, holder=lease.holder, event=lease.event, by=to, note=note, transfer=True
    )
    log.append(
        "lease.acquired",
        item_id,
        {
            "holder": to,
            "at": now,
            "ttl_s": lease.ttl_s,
            "globs": list(lease.globs),
            "worktree": lease.worktree,
            "branch": lease.branch,
            "note": f"{lease.note} [{note}]" if lease.note else note,
            "kind": kind,
            "resources": list(lease.resources),
            **_clone_field(log, to),
        },
    )


def release(
    log: EventLog, item_id: str, holder: str = "", note: str = "", forced: bool = False
) -> bool:
    """Give up a lease. Records WHOSE it was and WHO released it, which can differ —
    an operator releasing a crashed agent's lease is the normal case."""
    by = holder or log.agent_id
    owner: dict[str, str] = {}
    done = _transition(
        log,
        item_id,
        "lease.released",
        mine=False,
        holder=by,
        payload=lambda lease: (
            owner.update(holder=lease.holder)
            or _released(lease.holder, lease.event, by, note, forced=forced)
        ),
    )
    if done:
        _remote_drop(log, item_id, owner["holder"])
    return done


def foreign_live_lease(log: EventLog, cfg: Config, item_id: str, caller: str = "") -> Lease | None:
    """The LIVE lease on ``item_id`` when it was claimed from ANOTHER clone than this one
    (B90b43dd2d8), else None.

    A lease is evidence of intent, not a lock, so acting on another clone's live one is
    possible but must be asked for (`foreign_guard`). The scope is the CLONE, not the agent
    name (operator decision D-mu-bugfix-answers): the holder itself, any other agent of the
    same clone, an expired lease (judged on THIS clone's clock, B5a6651cd8e) and a lease with
    no recorded clone (claimed before the field existed) are never foreign. ``caller`` is the
    acting agent, kept for the message: the test is the clone's."""
    now = _leases.time.time()
    it = _fold(log).items.get(item_id)
    lease = it.lease if it else None
    if lease is None or not foreign(lease, log.clone_id(create=False)):
        return None
    return lease if lease.live(now, cfg.lease.grace_s) else None


def foreign_guard(
    log: EventLog, cfg: Config, item_id: str, *, force: bool, reason: str, what: str
) -> tuple[str, str]:
    """(refusal, forced-by) for an act on ``item_id``: ("", "") when nothing stands in the way,
    (text, "") to refuse (exit 3), ("", note) when ``--force`` + a reason overrides another
    clone's live lease -- ``note`` is the line to record with the act."""
    held = foreign_live_lease(log, cfg, item_id)
    if held is None:
        return "", ""
    who = f"{held.holder} (clone {held.clone})"
    base = (
        f"{item_id} is leased by {who} in another clone and the lease is live; "
        f"{what} would overwrite their work. "
    )
    if not force:
        return (
            base + "Ask them, wait for the lease to lapse, or override on the record: "
            "pass --force --reason '<why>'.",
            "",
        )
    if not reason.strip():
        return base + "--force on another clone's lease needs --reason '<why>' (recorded).", ""
    return "", f"forced over {who}: {reason.strip()}"


def expire(log: EventLog, item_id: str, reason: str = "") -> bool:
    """Record that a lease has expired. Idempotent: folding two expiries is one.

    Carries the worktree forward, because expiry is exactly when someone needs to know
    where the crashed agent's uncommitted work is.
    """
    owner: dict[str, str] = {}
    done = _transition(
        log,
        item_id,
        "lease.expired",
        mine=False,
        holder="",
        payload=lambda lease: (
            owner.update(holder=lease.holder)
            or {
                "holder": lease.holder,
                "event": lease.event,
                "reason": reason,
                "worktree": lease.worktree,
            }
        ),
    )
    if done:
        _remote_drop(log, item_id, owner["holder"])
    return done

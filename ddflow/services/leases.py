"""Leases — crash-recoverable ownership of a work item.

A lease is not a file and not a lock: it is an *event* with an expiry. That choice is
what makes crash recovery fall out for free. A lock file must be deleted by its holder,
so a killed holder leaves a lock nobody can safely remove. A lease simply stops being
renewed, and expiry is then a fact any observer can compute from the log.

Acquisition is check-then-act, so it runs inside an ``EventLog.transaction``: read the
current leases and append the claim under one lock. Without that span, two agents both
read "free" and both claim.

**Expiry never steals the work.** ``lease.reclaim_policy`` defaults to ``report``:
an expired lease is surfaced with the worktree path and the state of the tree, and a
human (or an agent following the recovery procedure) decides. The reason is empirical
— on the project that motivated ddflow, a killed agent's worktree was repeatedly found
to contain *finished* work that existed nowhere else. A system that auto-reclaims and
auto-deletes would have destroyed it. Recovery is: inspect, salvage, then release.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Config
from ..core import globspec as GS
from ..core import schedule
from ..core.flow import line_key
from ..core.model import DONE, REVIEW, Item, Lease, State, fold
from ..core.schedule import capacities, conflicts, plan_blocker, resource_shortfall
from ..infra import worktree as W
from ..infra.log import EventLog


class LeaseError(RuntimeError):
    """Raised when a lease cannot be acquired. Carries the blocking holder."""

    def __init__(
        self, msg: str, holder: str = "", item: str = "", alternatives: list[str] | None = None
    ) -> None:
        super().__init__(msg)
        self.holder, self.item = holder, item
        self.alternatives = alternatives or []


@dataclass
class Recovery:
    """One recoverable situation found by ``scan``."""

    item: str
    holder: str
    kind: str  # "expired_lease" | "orphan_worktree" | "stale_running"
    worktree: str = ""
    branch: str = ""
    dirty_files: int = 0
    unmerged_commits: int = 0
    age_s: float = 0.0
    advice: str = ""
    #: True = work found · False = measured clean · None = COULD NOT MEASURE.
    #: Three-valued on purpose. Collapsing "unknown" into "clean" made an unmeasurable
    #: worktree (broken gitdir, git absent, NFS stall) report "safe to remove" -- and
    #: `sweep(apply=True)` acts on exactly this flag.
    salvageable: bool | None = False
    #: The tree is the HARNESS's (`Item.adopted`): the agent's own live working
    #: directory, which ddflow bound to the item and did not create. Never advised for
    #: removal -- `merge` refuses to delete one for the same reason (B930f5c6b7c).
    adopted: bool = False


def _record_claimed_globs(
    log: EventLog, it: Item, globs: list[str] | None, resources: list[str] | None = None
) -> None:
    """Write the globs (and resources) a claim names onto its ITEM too, when they differ
    (Bbd07ab69fd, B7f8060f2f5).

    A claim's globs used to live on the lease alone. The heartbeat's catch-up
    (Bb21d338f26) points a renewed lease at the item's stored globs whenever the two
    differ, so the first heartbeat after `claim --globs` -- live, or reviving a lapsed
    lease (Bc5aec031b3) -- put the lease back on whatever the item had declared before,
    an empty list included. One answer to "what does this claim cover", recorded in the
    claim's own transaction, leaves the catch-up nothing to undo. Resources likewise,
    now that the catch-up covers them too.
    """
    fields: dict[str, list[str]] = {}
    if globs is not None and sorted(globs) != sorted(it.globs):
        fields["globs"] = list(globs)
    if resources is not None and sorted(resources) != sorted(it.resources):
        fields["resources"] = list(resources)
    if fields:
        log.append(f"{it.kind}.updated", it.id, fields)


def _renew_in_place(
    log: EventLog,
    existing: Lease,
    item_id: str,
    holder: str,
    now: float,
    worktree: str,
    branch: str,
    globs: list[str] | None,
    note: str,
    resources: list[str] | None = None,
) -> Lease:
    """Re-acquire by the current holder: a renewal that CARRIES THROUGH attachments.

    `claim` acquires the lease before the worktree exists and re-acquires to attach it,
    so an early return that ignored these fields left the lease permanently pointing at
    no worktree — and crash recovery then reported "nothing to salvage" over a tree
    full of uncommitted work.
    """
    upd: dict[str, object] = {"at": now, "holder": holder}
    if worktree:
        upd["worktree"] = worktree
    if branch:
        upd["branch"] = branch
    if globs is not None:
        upd["globs"] = list(globs)
    if note:
        upd["note"] = note
    if resources is not None:
        # Carried like globs. Dropped, a re-claim with `--resources gpu:8` exited 0
        # while the lease kept its old reservation (roborev 828).
        upd["resources"] = list(resources)
    log.append("lease.renewed", item_id, upd)
    existing.renewed_at = now
    existing.worktree = worktree or existing.worktree
    existing.branch = branch or existing.branch
    if globs is not None:
        existing.globs = list(globs)
    if resources is not None:
        existing.resources = list(resources)
    return existing


def _decide_from(log: EventLog) -> tuple[State, dict[str, int]]:
    """Fold the log OUTSIDE the append lock, with the extent it was folded at.

    Returns the state and the fingerprint to re-check once the lock is held. Paired with
    `_still_current`; see it for why this is safe.
    """
    before = log.extent()
    return fold(log.read_all(), strict=False), before


def _still_current(log: EventLog, state: State, before: dict[str, int]) -> State:
    """The state to DECIDE from, now that the lock is held.

    The invariant that matters is not "the fold happened inside the lock" — it is "the
    state the decision is made from reflects every event in the log at the moment of the
    append". Holding the lock across the read is one way to get that. Proving the log did
    not grow is another, and it holds the lock for a handful of `stat` calls instead of a
    full read of every shard.

    If anything DID grow, this re-folds inside the lock, which is exactly the old
    behaviour. So the slow path is never slower than before and the fast path — the
    overwhelmingly common one, since contention here is measured far below the point where
    it matters — skips the big read while under the lock.
    """
    if log.extent() == before:
        return state
    return fold(log.read_all(), strict=False)


def glob_clash(
    state: State, cfg: Config, it: Item, holder: str, globs: list[str], now: float
) -> tuple[str, Lease, tuple[str, str]] | None:
    """The first OTHER live lease ``globs`` would overlap: (item id, lease, pair), or None.

    One copy, asked by `claim` and by `update --globs` on a claimed item, so widening a
    claim cannot take paths that claiming them would have been refused.
    """
    my_line = line_key(state, it, cfg)
    for other_id, lease in state.active_leases(now, cfg.lease.grace_s).items():
        if other_id == it.id or lease.holder == holder:
            continue
        other = state.items.get(other_id)
        if other is not None and line_key(state, other, cfg) != my_line:
            continue  # different release lines: different branches, no collision
        pairs = conflicts(globs, lease.globs, schedule.shared_globs(cfg))
        if pairs:
            return other_id, lease, pairs[0]
    return None


def acquire(
    log: EventLog,
    cfg: Config,
    item_id: str,
    *,
    holder: str = "",
    **kwargs: Any,
) -> Lease:
    """Claim an item; with `[flow].claims = "remote"`, win the remote claim ref first.

    The remote round trips run BEFORE the log's append lock is taken -- up to six git
    subprocesses at 30 s each held under it would starve every local writer -- and a claim
    the local checks then refuse gives the ref back.
    """
    holder = holder or log.agent_id
    took = ""
    if cfg.flow.claims == "remote":
        took = _remote_take(log, cfg, item_id, holder, time.time())
    try:
        return _acquire_locked(log, cfg, item_id, holder=holder, **kwargs)
    except Exception:
        if took == "fresh":  # an own, already-live claim is not ours to give back here
            _remote_drop(log, item_id, holder)
        raise


def _acquire_locked(
    log: EventLog,
    cfg: Config,
    item_id: str,
    *,
    holder: str = "",
    globs: list[str] | None = None,
    worktree: str = "",
    branch: str = "",
    note: str = "",
    force: bool = False,
    resources: list[str] | None = None,
) -> Lease:
    """Claim an item. Raises ``LeaseError`` (never steals) if someone live holds it.

    The read that decides and the write that claims cannot be separated by another
    agent's claim. That used to be achieved by folding the whole log INSIDE the lock;
    it is now achieved by folding outside and proving, under the lock, that the log did
    not grow — see `_still_current`. Same guarantee, and the lock is held across `stat`
    calls rather than across a read of every shard.
    """
    holder = holder or log.agent_id
    now = time.time()
    snapshot, before = _decide_from(log)
    with log.transaction():
        state = _still_current(log, snapshot, before)
        it = state.items.get(item_id)
        if it is None:
            raise LeaseError(f"no such item {item_id!r}", item=item_id)
        if it.removed:
            raise LeaseError(f"{item_id} was removed", item=item_id)
        bad = GS.problem(globs or [])
        if bad:
            raise LeaseError(f"{item_id}: {bad}", item=item_id)
        if it.state == DONE and not force:
            # An agent decides what to claim from a snapshot it folded a moment ago.
            # Between that fold and this acquire, another agent can finish the item --
            # and without this check the second agent cheerfully re-does completed
            # work, which is the exact duplicate-work failure the queue exists to stop.
            # Found by the 8-process stress test: 50 claims for 32 tasks.
            raise LeaseError(
                f"{item_id} is already done"
                + (f" (merged as {it.merged_sha})" if it.merged_sha else "")
                + ". Re-open it deliberately with --force if you mean to redo it.",
                item=item_id,
                alternatives=_alternatives(state, cfg, item_id, holder, now),
            )

        existing = it.lease
        if existing and not existing.expired(now, cfg.lease.grace_s):
            if existing.holder == holder:
                _record_claimed_globs(log, it, globs, resources)
                return _renew_in_place(
                    log, existing, item_id, holder, now, worktree, branch, globs, note, resources
                )
            raise LeaseError(
                f"{item_id} is held by {existing.holder} for another "
                f"{existing.remaining_s(now):.0f}s",
                holder=existing.holder,
                item=item_id,
                alternatives=_alternatives(state, cfg, item_id, holder, now),
            )
        if existing and not force and cfg.lease.reclaim_policy == "report":
            raise LeaseError(
                f"{item_id} has an EXPIRED lease from {existing.holder} "
                f"(worktree {existing.worktree or '-'}). It is NOT stolen automatically: "
                f"a crashed agent's worktree often holds finished work. "
                f"Run `ddflow recover --item {item_id}`, then retry with --force.",
                holder=existing.holder,
                item=item_id,
            )

        # Dependencies, cycles and umbrellas, asked of the SAME predicate the
        # scheduler uses. Without this, `ddflow next` refused an item and
        # `ddflow claim <that item>` granted it a worktree a second later, so any
        # agent choosing work by id rather than by asking `next` bypassed the
        # dependency graph entirely.
        blocked = plan_blocker(state, cfg, it)
        if blocked is not None and not force:
            raise LeaseError(
                f"{item_id} is not ready: {blocked.reason} — {blocked.detail}"
                + (
                    "\nUse --force only if you mean to start it anyway."
                    if blocked.reason != "umbrella"
                    else ""
                ),
                item=item_id,
                alternatives=(
                    blocked.waiting_on
                    if blocked.reason == "umbrella"
                    else _alternatives(state, cfg, item_id, holder, now)
                ),
            )

        mine = list(globs if globs is not None else it.globs)
        clash = glob_clash(state, cfg, it, holder, mine, now)
        if clash and not force:
            other_id, lease, pair = clash
            raise LeaseError(
                f"{item_id} writes {pair[0]!r} which overlaps {pair[1]!r} "
                f"held by {lease.holder} on {other_id}",
                holder=lease.holder,
                item=item_id,
                alternatives=_alternatives(state, cfg, item_id, holder, now),
            )

        # Resources: checked against EVERY live lease, the claimant's own included --
        # the same agent starting two 8-GPU runs on an 8-GPU box still overcommits it.
        # Inside the transaction for the same reason as globs: two agents reading "4
        # free" and both taking 4 is the check-then-act race the lock exists for.
        wants = list(resources if resources is not None else it.resources)
        if wants and not force:
            try:
                short = resource_shortfall(
                    wants,
                    state.active_leases(now, cfg.lease.grace_s),
                    capacities(cfg),
                    exclude=item_id,
                )
            except ValueError as exc:
                # A CONFIG or declaration error, not a conflict: "wait for one to be
                # released" cannot help (roborev 828).
                raise LeaseError(f"{item_id}: {exc}", item=item_id) from exc
            if short:
                raise LeaseError(
                    f"{item_id} {short}. Wait for one to be released, or take something "
                    f"that does not need it.",
                    item=item_id,
                    alternatives=_alternatives(state, cfg, item_id, holder, now),
                )

        _record_claimed_globs(log, it, globs, resources)
        log.append(
            "lease.acquired",
            item_id,
            {
                "holder": holder,
                "at": now,
                "ttl_s": cfg.lease.ttl_s,
                "globs": mine,
                "worktree": worktree,
                "branch": branch,
                "note": note,
                "kind": it.kind,
                "resources": wants,
            },
        )
        return Lease(
            holder=holder,
            acquired_at=now,
            renewed_at=now,
            ttl_s=cfg.lease.ttl_s,
            worktree=worktree,
            branch=branch,
            globs=mine,
            note=note,
            resources=wants,
        )


def _alternatives(state: State, cfg: Config, item_id: str, holder: str, now: float) -> list[str]:
    """Items this agent COULD take instead.

    Delegates to the scheduler rather than re-deriving readiness, and suggests only
    items of the SAME kind as the one refused. A forked copy lived here and had already
    drifted: it ignored `schedule.unknown_dep_policy`, so a refused agent was told to
    take an item the scheduler would then also refuse. A refusal that recommends an
    impossible alternative is worse than one that recommends nothing.
    """
    from ..core.schedule import plan as _plan

    refused = state.items.get(item_id)
    kind = refused.kind if refused else "task"
    return sorted(
        it.id
        for it in _plan(state, cfg, kind=kind, now=now, agent=holder).ready
        if it.id != item_id
    )[:5]


def _remote_take(log: EventLog, cfg: Config, item_id: str, holder: str, now: float) -> str:
    """Win the remote claim ref or refuse the claim. Returns "fresh" for a new ref and
    "renewed" when we already held it."""
    from ..infra import claimref as CR

    got = CR.take(log.root, cfg.flow.remote, item_id, holder, now + cfg.lease.ttl_s)
    if got.status == "held":
        raise LeaseError(
            f"{item_id} is claimed on the remote {cfg.flow.remote!r} by {got.holder or 'another clone'}"
            + (f" for another {max(0, got.expires - now):.0f}s" if got.expires else ""),
            holder=got.holder,
            item=item_id,
        )
    if got.status != "ok":
        raise LeaseError(
            f"{item_id}: [flow].claims = 'remote' but the remote claim could not be made "
            f"({got.detail or 'remote unavailable'}). Not claiming locally instead: that is "
            f"the silent split-brain this setting exists to prevent.",
            item=item_id,
        )
    return "renewed" if got.detail == "renewed" else "fresh"


def _remote_renew(log: EventLog, cfg: Config, item_id: str, holder: str, now: float) -> str:
    """Extend our remote claim. Returns the holder who has it instead of us ("" when ours,
    or when the remote could not be asked: it then lapses at its expiry and the next
    successful renew re-takes it)."""
    if cfg.flow.claims != "remote":
        return ""
    from ..infra import claimref as CR

    got = CR.renew(log.root, cfg.flow.remote, item_id, holder, now + cfg.lease.ttl_s)
    return (got.holder or "another clone") if got.status == "held" else ""


def _remote_drop(log: EventLog, item_id: str, holder: str) -> None:
    """Best effort: a ref we fail to delete lapses with its expiry and is replaced then."""
    from ..config import Config

    cfg = Config.load(log.root)
    if cfg.flow.claims == "remote":
        from ..infra import claimref as CR

        CR.drop(log.root, cfg.flow.remote, item_id, holder)


def _transition(
    log: EventLog,
    item_id: str,
    kind: str,
    *,
    mine: bool,
    holder: str,
    payload: Callable[[Lease], dict[str, Any]],
) -> bool:
    """Append one lease-lifecycle event, under the lock, if the lease is in a fit state.

    `renew`, `release` and `expire` were ~85% one body: open a transaction, fold, find
    the item, check it has a lease, append. They differed in a guard (renew requires the
    lease to be MINE; the other two do not) and in a payload. Three copies of a
    read-then-write under a lock is three chances for one of them to drift out of the
    lock — and the copies had already drifted in whether they recorded the holder.

    The read which decides and the write which acts must not be separated by another
    agent's append. That is held by `_still_current` rather than by folding inside the
    lock: the fold happens outside, and under the lock the log is PROVED not to have
    grown. Where it has, it is re-folded there, which is the old behaviour exactly.
    """
    holder = holder or log.agent_id
    snapshot, before = _decide_from(log)
    with log.transaction():
        state = _still_current(log, snapshot, before)
        it = state.items.get(item_id)
        if not it or not it.lease:
            return False
        if mine and it.lease.holder != holder:
            return False
        log.append(kind, item_id, payload(it.lease))
        return True


def renew(log: EventLog, item_id: str, holder: str = "") -> bool:
    """Extend MY lease. Refuses on someone else's: a renewal is a claim of possession."""
    holder = holder or log.agent_id
    ok = _transition(
        log,
        item_id,
        "lease.renewed",
        mine=True,
        holder=holder,
        payload=lambda _lease: {"at": time.time(), "holder": holder},
    )
    if ok:
        from ..config import Config

        # A claim someone else took over on the remote is not renewed: False, as for any
        # lease we no longer hold.
        return not _remote_renew(log, Config.load(log.root), item_id, holder, time.time())
    return ok


def plan_retarget(
    log: EventLog,
    cfg: Config,
    item_id: str,
    globs: list[str] | None,
    resources: list[str] | None = None,
) -> tuple[Lease | None, str]:
    """(the LIVE lease to point at ``globs`` / ``resources`` or None, why that is refused
    or "").

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
    now = time.time()
    state = fold(log.read_all(), strict=False)
    it = state.items.get(item_id)
    lz = it.lease if it else None
    if it is None or lz is None or lz.expired_at or lz.expired(now, cfg.lease.grace_s):
        return None, ""
    clash = glob_clash(state, cfg, it, lz.holder, globs, now) if globs is not None else None
    if clash:
        other_id, other, pair = clash
        return lz, (
            f"{item_id} is claimed, so its globs are its lease: {pair[0]!r} overlaps "
            f"{pair[1]!r} held by {other.holder} on {other_id}. Nothing was recorded. "
            f"Wait for {other_id} to finish, or keep {item_id} off those paths."
        )
    if resources:
        try:
            short = resource_shortfall(
                list(resources),
                state.active_leases(now, cfg.lease.grace_s),
                capacities(cfg),
                exclude=item_id,
            )
        except ValueError as exc:
            return lz, f"{item_id}: {exc}. Nothing was recorded."
        if short:
            return lz, (
                f"{item_id} is claimed, so its resources are its lease's reservation: "
                f"{item_id} {short}. Nothing was recorded. Wait for one to be released, "
                f"or ask for less."
            )
    return lz, ""


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


def release(log: EventLog, item_id: str, holder: str = "", note: str = "") -> bool:
    """Give up a lease. Records WHOSE it was and WHO released it, which can differ —
    an operator releasing a crashed agent's lease is the normal case."""
    by = holder or log.agent_id
    owner: list[str] = []
    done = _transition(
        log,
        item_id,
        "lease.released",
        mine=False,
        holder=by,
        # `event` names WHICH claim ends: one holder can have held the item twice, and a
        # fold that merges clones must not end the wrong one (B191).
        payload=lambda lease: (
            owner.append(lease.holder)
            or {
                "holder": lease.holder,
                "event": lease.event,
                "by": by,
                "note": note,
            }
        ),
    )
    if done:
        _remote_drop(log, item_id, owner[0])
    return done


def expire(log: EventLog, item_id: str, reason: str = "") -> bool:
    """Record that a lease has expired. Idempotent: folding two expiries is one.

    Carries the worktree forward, because expiry is exactly when someone needs to know
    where the crashed agent's uncommitted work is.
    """
    owner: list[str] = []
    done = _transition(
        log,
        item_id,
        "lease.expired",
        mine=False,
        holder="",
        payload=lambda lease: (
            owner.append(lease.holder)
            or {
                "holder": lease.holder,
                "event": lease.event,
                "reason": reason,
                "worktree": lease.worktree,
            }
        ),
    )
    if done:
        _remote_drop(log, item_id, owner[0])
    return done


# -- recovery ------------------------------------------------------------------------


def scan(log: EventLog, cfg: Config, repo: Path, *, now: float | None = None) -> list[Recovery]:
    """Find everything a crash could have left behind, and say what to do with each.

    Three distinct situations, deliberately not merged:

    * **expired_lease** — the holder stopped renewing. Its worktree may hold work.
    * **orphan_worktree** — a worktree exists for an item with no live lease.
    * **stale_running** — the item is RUNNING with no lease at all: the agent died
      between starting and claiming, or someone released without finishing.

    Each carries the measured state of the tree (dirty files, unmerged commits), because
    "is there work in here?" is the only question that decides the remedy, and guessing
    it from the item's status is exactly how real work gets deleted.
    """
    now = time.time() if now is None else now
    state = fold(log.read_all(), strict=False)
    # The same live-lease view the scheduler computes, so `schedule.interrupted` is
    # answering about the same moment `next` would.
    live = state.active_leases(now, cfg.lease.grace_s)
    out: list[Recovery] = []

    for it in state.items.values():
        if it.removed:
            continue
        lease = it.lease
        if lease and lease.expired(now, cfg.lease.grace_s):
            # Prefer whichever source actually names a tree. These should agree, and
            # after the renew fix they do -- but if they ever diverge, trusting the
            # empty one produces "nothing to salvage" over real work, which is the one
            # error this function must never make.
            rec = Recovery(
                item=it.id,
                holder=lease.holder,
                kind="expired_lease",
                adopted=it.adopted,
                worktree=lease.worktree or it.worktree,
                branch=lease.branch or it.branch,
                age_s=now - lease.renewed_at,
            )
            _measure(rec, repo, cfg)
            out.append(rec)
        elif not lease and it.worktree and it.state != REVIEW:
            # REVIEW is excluded: its tree is held open ON PURPOSE, for the round of
            # changes a reviewer may request, and the request is its custodian. Reported
            # as an orphan it led every brief as "salvage this" (RESEARCH R16).
            rec = Recovery(
                item=it.id,
                holder="(none)",
                kind="orphan_worktree",
                adopted=it.adopted,
                worktree=it.worktree,
                branch=it.branch,
            )
            _measure(rec, repo, cfg)
            out.append(rec)
        elif schedule.interrupted(state, it, live):
            # Same predicate the scheduler uses, so `recover` and `next` cannot
            # disagree about which items are mid-flight with nobody on them.
            rec = Recovery(
                item=it.id,
                holder="(none)",
                kind="stale_running",
                advice=f"`ddflow claim {it.id}` to resume, or `ddflow block {it.id} --reason ...`",
            )
            out.append(rec)
    return sorted(out, key=lambda r: (r.kind, r.item))


def _measure(rec: Recovery, repo: Path, cfg: Config) -> None:
    """Measure the worktree rather than trusting the item's recorded state.

    'Dirty' alone is NOT evidence of unshipped work — a dirty tree is just as often a
    superseded draft. So we report both halves (uncommitted files AND commits not on
    the base branch) and let the operator judge, with the diff command spelled out.

    Git access goes through ``worktree.py``, which owns it. This module briefly carried
    its own copy, and the copy drifted: its branch resolver fell back to the literal
    string ``"HEAD"`` where the real one falls back to the current branch name. On any
    repository whose default branch is not ``main`` or ``master`` that turned the
    unmerged-commit count into ``rev-list HEAD..HEAD`` = 0, so a tree holding unmerged
    work was reported "safe to remove" — the one error this function must never make.
    """
    # Only an expired lease is still there to release. An orphan's lease is already
    # null, and telling someone to release it sends them to an exit 2 (B4f9019c0d7).
    held = rec.kind == "expired_lease"
    wt = W.load_path(repo, rec.worktree) if rec.worktree else None
    if not wt or not wt.exists():
        rec.advice = "worktree is gone; nothing to salvage" + (
            f". `ddflow release {rec.item}` to clear the claim."
            if held
            else ", and no lease is held on it."
        )
        return
    base = cfg.worktree.base_ref or W.default_branch(repo)
    probe = W.Worktree(item=rec.item, path=wt, branch=rec.branch, base=base)
    status = W.git(wt, "status", "--porcelain")
    rec.dirty_files = len([ln for ln in status.out.splitlines() if ln.strip()]) if status.ok else -1
    rec.unmerged_commits = W.ahead(probe)
    if rec.dirty_files < 0 or rec.unmerged_commits < 0:
        rec.salvageable = None
        rec.advice = (
            f"COULD NOT MEASURE this worktree (git returned "
            f"{status.err or 'an error'!r:.80}). Treat it as containing work until "
            f"you have looked: `git -C {wt} status` and `git -C {wt} log {base}..HEAD`. "
            f"It will NOT be swept automatically."
        )
        return
    rec.salvageable = rec.dirty_files > 0 or rec.unmerged_commits > 0
    if rec.salvageable:
        rec.advice = (
            f"INSPECT FIRST — {rec.dirty_files} uncommitted file(s), "
            f"{rec.unmerged_commits} unmerged commit(s). "
            f"`git -C {wt} diff {base}` then salvage"
            + (f", then `ddflow release {rec.item} --note salvaged`." if held else ".")
            + (
                " The tree is adopted: the agent harness's own working tree, not "
                "ddflow's -- salvage from it and leave the tree to the harness."
                if rec.adopted
                else ""
            )
        )
    elif rec.adopted:
        rec.advice = (
            "clean and fully merged, but adopted: this is the agent harness's own working "
            "tree, not ddflow's -- leave it to the harness. "
        ) + (
            f"`ddflow release {rec.item}` is all ddflow needs."
            if held
            else "No lease is held on it."
        )
    else:
        # `git worktree remove`, not `ddflow cleanup --apply`: that one removes EVERY
        # merged tree, a live agent's freshly claimed one included (B5a009b185a).
        rec.advice = f"clean and fully merged — safe to remove: `git worktree remove {wt}`" + (
            f" then `ddflow release {rec.item}`." if held else " (no lease is held on it)."
        )


def sweep(log: EventLog, cfg: Config, repo: Path, *, apply: bool = False) -> list[Recovery]:
    """Report recoverable state; with ``apply``, record expiry for the UNSALVAGEABLE
    ones only. Salvageable trees are never touched automatically, whatever the policy —
    the knob controls convenience, not safety."""
    found = scan(log, cfg, repo)
    if apply:
        for rec in found:
            # `is False` NOT `not ...` -- None means "could not measure" and must never
            # be swept. `not None` is True, which is how unknown became clean.
            if rec.kind == "expired_lease" and rec.salvageable is False:
                expire(log, rec.item, reason=f"swept: {rec.advice}")
    return found

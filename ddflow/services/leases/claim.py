"""Claim an item: the transaction-safe read-then-act under the append lock.

Split out of ``ddflow.services.leases``: ``acquire``, its ``_acquire_locked``
transaction body, and the idle-lease breaking that runs before it.
"""

from __future__ import annotations

from typing import Any

from ...config import Config
from ...core import clock, schedule
from ...core import globspec as GS
from ...core.model import DONE, Item, Lease, State
from ...core.schedule import capacities, plan_blocker, resource_shortfall
from ...core.schedule import plan as _plan
from ...infra.log import EventLog
from .. import identity as _ID
from .. import leasebreak as LB
from .. import leases as _leases
from .common import _clone_field, _fold, clash_report, glob_clash, glob_clashes
from .model import LeaseError, Offer
from .remote import _remote_drop, _remote_renew, _remote_take


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
        log.append(f"{it.family}.updated", it.id, fields)


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
        if _holds_live(log, cfg, item_id, holder):
            # Re-asserting a live claim of our own: renewing tolerates an unreachable
            # remote (it lapses at its expiry and the next heartbeat re-takes it), as the
            # heartbeat does; only a claim someone else took over is refused.
            if lost := _remote_renew(log, cfg, item_id, holder, _leases.time.time()):
                raise LeaseError(
                    f"{item_id}: the remote claim is now {lost}'s -- this lease lapsed "
                    f"there and was taken over.",
                    holder=lost,
                    item=item_id,
                )
        else:
            took = _remote_take(log, cfg, item_id, holder, _leases.time.time())
    try:
        return _acquire_locked(log, cfg, item_id, holder=holder, **kwargs)
    except Exception:
        if took == "fresh":  # an own, already-live claim is not ours to give back here
            _remote_drop(log, item_id, holder)
        raise


def _holds_live(log: EventLog, cfg: Config, item_id: str, holder: str) -> bool:
    it = _fold(log).items.get(item_id)
    lease = it.lease if it else None
    return bool(
        lease and lease.holder == holder and lease.live(_leases.time.time(), cfg.lease.grace_s)
    )


def _acquire_locked(  # noqa: PLR0913 -- the claim's own fields plus the caller's offer
    log: EventLog,
    cfg: Config,
    item_id: str,
    *,
    holder: str = "",
    agent: str = "",
    globs: list[str] | None = None,
    worktree: str = "",
    branch: str = "",
    note: str = "",
    force: bool = False,
    resources: list[str] | None = None,
    offer: Offer | None = None,
    break_lease: bool = False,
    activity: LB.Activity | None = None,
    broke: list[dict[str, Any]] | None = None,
) -> Lease:
    """Claim an item. Raises ``LeaseError`` (never steals) if someone live holds it. A refusal
    names what the caller could take instead: ``offer`` says (the API hands in the offer
    `next` makes, reservations and limit included); without one it is the bare scheduler's.

    The read that decides and the write that claims cannot be separated by another
    agent's claim. That used to be achieved by folding the whole log INSIDE the lock;
    it is now achieved by folding outside and proving, under the lock, that the log did
    not change — see `EventLog.decide_then_append`. Same guarantee, and the lock is held
    across `stat` calls rather than across a read of every shard.
    """
    holder = holder or log.agent_id
    now = _leases.time.time()
    pre = _leases._prejudge(log, cfg, item_id, holder, globs, now, break_lease, activity)
    with log.decide_then_append(lambda: _fold(log)) as state:
        it = state.items.get(item_id)
        if it is None:
            raise LeaseError(f"no such item {item_id!r}", item=item_id)
        if it.removed:
            raise LeaseError(f"{item_id} was removed", item=item_id)
        bad = GS.problem(globs or []) or (schedule.problem(resources) if resources else "")
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
                alternatives=_alts(offer, state, cfg, item_id, holder, now),
            )

        existing = it.lease
        if existing and existing.live(now, cfg.lease.grace_s):
            if existing.holder == holder:
                _record_claimed_globs(log, it, globs, resources)
                return _renew_in_place(
                    log, existing, item_id, holder, now, worktree, branch, globs, note, resources
                )
            # An agent's `--force` is dropped at the API layer (its route is
            # `--approval request`), so the take-over route named here is the one that
            # applies to WHO is asking, never `--force` to an agent (Bbb8c158254).
            take_over = (
                f"`ddflow claim {item_id} --approval request`"
                if _ID.agent_marker(agent)
                else f"`ddflow claim {item_id} --force`"
            )
            left = existing.remaining_s(now)
            raise LeaseError(
                f"{item_id} is held by {existing.holder} for another {left:.0f}s"
                if left >= 0
                else f"{item_id}: {existing.holder}'s lease ran out {-left:.0f}s ago and is in "
                f"its {cfg.lease.grace_s}s grace period; it can be taken over "
                f"(`ddflow recover --item {item_id}`, then {take_over}) "
                f"in {cfg.lease.grace_s + left:.0f}s",
                holder=existing.holder,
                item=item_id,
                alternatives=_alts(offer, state, cfg, item_id, holder, now),
            )
        # Only reached for a lease that is NOT live (a live own one returned above), so a
        # holder's own lease here is, by construction, a lapsed one.
        own_lapsed = existing is not None and existing.holder == holder and cfg.lease.resume_own
        if existing and not force and not own_lapsed and cfg.lease.reclaim_policy == "report":
            if existing.expired_at:
                # Recorded by `recover --apply`, which records only a tree it measured
                # EMPTY. Sending the agent back to `recover` sent it round in a circle:
                # the take-back is still deliberate (help/recovery.md), and safe
                # (Bc58ea6b7a7).
                raise LeaseError(
                    f"{item_id} has an EXPIRED lease from {existing.holder}, and its "
                    f"expiry was recorded at {existing.expired_at}: recover measured its "
                    f"tree (worktree {existing.worktree or '-'}) then, with nothing to "
                    f"salvage. Taking it over is deliberate, and safe as of that sweep: "
                    f"{take_over} adopts the tree as it is now.",
                    holder=existing.holder,
                    item=item_id,
                )
            raise LeaseError(
                f"{item_id} has an EXPIRED lease from {existing.holder} "
                f"(worktree {existing.worktree or '-'}). It is NOT stolen automatically: "
                f"a crashed agent's worktree often holds finished work. "
                f"To take it over: `ddflow recover --item {item_id}`, then "
                f"{take_over}.",
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
                    else _alts(offer, state, cfg, item_id, holder, now)
                ),
            )

        mine = list(globs if globs is not None else it.globs)
        clash = glob_clash(state, cfg, it, holder, mine, now)
        if clash and not (force and existing is None):
            clash = _break_idle(
                log, cfg, state, it, holder, mine, now, clash, break_lease, activity, broke, pre
            )
        # Taking over an EXPIRED lease -- the holder's own or a crashed agent's -- needs
        # --force for the expiry alone. It must not also waive the overlap check: expiry
        # freed those files, and a live lease granted on them since stands (B08b6e40bfb).
        if clash and (not force or existing is not None):
            # `clash` is what `_break_idle` left standing (a non-breakable lease): the hint
            # must name IT, while `glob_clashes` re-lists every live clash for the message.
            other_id, lease, _pair = clash
            named, _ids = clash_report(glob_clashes(state, cfg, it, holder, mine, now))
            raise LeaseError(
                f"{item_id} writes files another live lease holds: {named}"
                + (
                    f". {item_id}'s lease lapsed and those files were claimed since: "
                    f"--force takes over the expired lease, not another live one: "
                    f"{lease.holder} releases {other_id}, or you wait for it."
                    + (
                        " Claim again with --globs naming only the files nobody holds."
                        if own_lapsed
                        else ""
                    )
                    if existing is not None
                    else (
                        f". Claim with `ddflow claim {item_id} --globs <files>` naming "
                        "only the files your work really writes: the item's declared "
                        "globs are refused only where another lease holds them, so a "
                        "narrower set can be claimed at once."
                    )
                ),
                holder=lease.holder,
                item=item_id,
                alternatives=_alts(offer, state, cfg, item_id, holder, now),
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
                    alternatives=_alts(offer, state, cfg, item_id, holder, now),
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
                **_clone_field(log, holder),
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


def _prejudge(
    log: EventLog,
    cfg: Config,
    item_id: str,
    holder: str,
    globs: list[str] | None,
    now: float,
    asked: bool,
    activity: LB.Activity | None,
) -> LB.Prejudged | None:
    """Read, OUTSIDE the append lock, what `_break_idle` will judge idleness from: the log's
    events and the worktree activity of every lease this claim clashes with. The git calls
    behind that activity are the slow part; made under the lock they stall every other
    writer. None when breaking is off or nothing clashes."""
    if not (asked or cfg.lease.break_on_timeout) or not cfg.lease.hold_max_s:
        return None
    mark = log.mark(clock=False)
    state = _fold(log)
    it = state.items.get(item_id)
    if it is None:
        return None
    mine = list(globs if globs is not None else it.globs)
    pre, act = (
        LB.Prejudged(mark=mark, at=_leases.time.time()),
        activity or LB.tree_activity(log.root),
    )
    stamp = clock.iso_at(now)
    clash = glob_clash(state, cfg, it, holder, mine, now)
    while clash:
        lease = clash[1]
        pre.seen[pre.key(lease)] = act(lease)
        lease.expired_at = stamp  # this throwaway snapshot no longer sees it live
        clash = glob_clash(state, cfg, it, holder, mine, now)
    if not pre.seen:
        return None
    pre.events = log.read_all()
    return pre


def _break_idle(  # noqa: PLR0913, PLR0917 -- the claim's own view of the clash
    log: EventLog,
    cfg: Config,
    state: State,
    it: Item,
    holder: str,
    globs: list[str],
    now: float,
    clash: tuple[str, Lease, tuple[str, str]] | None,
    asked: bool,
    activity: LB.Activity | None,
    broke: list[dict[str, Any]] | None,
    pre: LB.Prejudged | None = None,
):
    """Break every clashing lease that is contended (this claim contends it) and has shown no
    progress for `[lease].hold_max_s`, when `[lease].break_on_timeout` or ``asked``
    (`claim --break`) allows it; the clash that remains, or None. Each break is a logged
    `lease.expired` naming who broke it; the holder's branch and worktree are kept
    (`services.leasebreak`)."""
    if not (asked or cfg.lease.break_on_timeout) or not cfg.lease.hold_max_s:
        return clash
    act = activity or LB.tree_activity(log.root)
    # Judged from what was read before the lock. The events are reused while the log has not
    # moved (else re-read: a fold, no git); the worktree watermarks while they are young.
    if pre is not None and pre.fresh(_leases.time.time()):
        act = pre.activity(act)
    events = pre.events if pre and pre.mark == log.mark(clock=False) else log.read_all()
    plan: list[tuple[str, Lease, tuple[str, str], LB.Verdict]] = []
    stamp = clock.iso_at(now)
    try:
        while clash:  # decide ALL of it before any write: a half-broken claim helps no one
            other_id, lease, pair = clash
            v = LB.judge(cfg, events, other_id, lease, [it.id], now, act, log.root)
            if v is None or not v.breakable:
                if asked:
                    raise LeaseError(
                        f"{it.id} writes {pair[0]!r} held by {lease.holder} on {other_id}"
                        + LB.break_hint(cfg),
                        holder=lease.holder,
                        item=it.id,
                    )
                for _o, held, _p, _v in plan:
                    held.expired_at = ""
                return clash
            plan.append((other_id, lease, pair, v))
            lease.expired_at = stamp  # this snapshot no longer sees it live
            clash = glob_clash(state, cfg, it, holder, globs, now)
    except LeaseError:
        for _o, lease, _p, _v in plan:
            lease.expired_at = ""
        raise
    if clash is not None:
        return clash
    for other_id, lease, pair, v in plan:
        log.append("lease.expired", other_id, LB.breaking_record(lease, it.id, holder, pair, v))
        _remote_drop(log, other_id, lease.holder)
        if broke is not None:
            broke.append({"item": other_id, "holder": lease.holder, "idle_s": round(v.idle_s, 1)})
    return None


def _alts(
    offer: Offer | None, state: State, cfg: Config, item_id: str, holder: str, now: float
) -> list[str]:
    return (
        offer(state, item_id, holder, now)
        if offer is not None
        else _alternatives(state, cfg, item_id, holder, now)
    )


def _alternatives(state: State, cfg: Config, item_id: str, holder: str, now: float) -> list[str]:
    """Items this agent COULD take instead.

    Delegates to the scheduler rather than re-deriving readiness, and suggests only
    items of the SAME kind as the one refused. A forked copy lived here and had already
    drifted: it ignored `schedule.unknown_dep_policy`, so a refused agent was told to
    take an item the scheduler would then also refuse. A refusal that recommends an
    impossible alternative is worse than one that recommends nothing.
    """

    refused = state.items.get(item_id)
    kind = refused.kind if refused else "task"
    return sorted(
        it.id
        for it in _plan(state, cfg, kind=kind, now=now, agent=holder).ready
        if it.id != item_id
    )[:5]

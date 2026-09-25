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
from ..core import schedule
from ..core.model import DONE, Lease, State, fold
from ..core.schedule import conflicts, plan_blocker
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
    log.append("lease.renewed", item_id, upd)
    existing.renewed_at = now
    existing.worktree = worktree or existing.worktree
    existing.branch = branch or existing.branch
    if globs is not None:
        existing.globs = list(globs)
    return existing


def acquire(
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
) -> Lease:
    """Claim an item. Raises ``LeaseError`` (never steals) if someone live holds it.

    The whole body runs under one lock, so the read that decides and the write that
    claims cannot be separated by another agent's claim.
    """
    holder = holder or log.agent_id
    now = time.time()
    with log.transaction():
        state = fold(log.read_all(), strict=False)
        it = state.items.get(item_id)
        if it is None:
            raise LeaseError(f"no such item {item_id!r}", item=item_id)
        if it.removed:
            raise LeaseError(f"{item_id} was removed", item=item_id)
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
                return _renew_in_place(
                    log, existing, item_id, holder, now, worktree, branch, globs, note
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
        for other_id, lease in state.active_leases(now, cfg.lease.grace_s).items():
            if other_id == item_id or lease.holder == holder:
                continue
            pairs = conflicts(mine, lease.globs)
            if pairs and not force:
                raise LeaseError(
                    f"{item_id} writes {pairs[0][0]!r} which overlaps {pairs[0][1]!r} "
                    f"held by {lease.holder} on {other_id}",
                    holder=lease.holder,
                    item=item_id,
                    alternatives=_alternatives(state, cfg, item_id, holder, now),
                )

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

    The fold must stay INSIDE the transaction: the whole point is that the read which
    decides and the write which acts cannot be separated by another agent's append.
    """
    holder = holder or log.agent_id
    with log.transaction():
        state = fold(log.read_all(), strict=False)
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
    return _transition(
        log,
        item_id,
        "lease.renewed",
        mine=True,
        holder=holder,
        payload=lambda _lease: {"at": time.time(), "holder": holder},
    )


def release(log: EventLog, item_id: str, holder: str = "", note: str = "") -> bool:
    """Give up a lease. Records WHOSE it was and WHO released it, which can differ —
    an operator releasing a crashed agent's lease is the normal case."""
    by = holder or log.agent_id
    return _transition(
        log,
        item_id,
        "lease.released",
        mine=False,
        holder=by,
        payload=lambda lease: {"holder": lease.holder, "by": by, "note": note},
    )


def expire(log: EventLog, item_id: str, reason: str = "") -> bool:
    """Record that a lease has expired. Idempotent: folding two expiries is one.

    Carries the worktree forward, because expiry is exactly when someone needs to know
    where the crashed agent's uncommitted work is.
    """
    return _transition(
        log,
        item_id,
        "lease.expired",
        mine=False,
        holder="",
        payload=lambda lease: {
            "holder": lease.holder,
            "reason": reason,
            "worktree": lease.worktree,
        },
    )


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
                worktree=lease.worktree or it.worktree,
                branch=lease.branch or it.branch,
                age_s=now - lease.renewed_at,
            )
            _measure(rec, repo, cfg)
            out.append(rec)
        elif not lease and it.worktree:
            rec = Recovery(
                item=it.id,
                holder="(none)",
                kind="orphan_worktree",
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
    wt = W.load_path(repo, rec.worktree) if rec.worktree else None
    if not wt or not wt.exists():
        rec.advice = (
            "worktree is gone; nothing to salvage. "
            f"`ddflow lease release {rec.item}` to clear the claim."
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
            f"`git -C {wt} diff {base}` then salvage, "
            f"then `ddflow lease release {rec.item} --note salvaged`."
        )
    else:
        rec.advice = (
            f"clean and fully merged — safe to remove: "
            f"`ddflow worktree remove {rec.item}` "
            f"then `ddflow lease release {rec.item}`."
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

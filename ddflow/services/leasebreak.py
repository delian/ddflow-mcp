"""A contended lease that shows no progress is breakable (D-lease-hold-limit).

Liveness is not progress: on 2026-10-15 three agents sat for 45 minutes holding current
leases because a heartbeat loop renews an idle lease forever. So idleness is measured by
the work, never by the heartbeat. A lease's *progress watermark* is the newest of

* any event on the item that is not lease bookkeeping (a gate outcome, a note, a job start),
* the newest commit its branch has that the base lacks,
* the newest uncommitted change in its worktree,
* the moment the lease was acquired.

A lease is judged only while CONTENDED: another item's recorded wait, or a claim being
made now, overlaps its globs. Uncontended, it is never yield-requested or broken however
old. Contended and idle for `[lease].idle_max_s` it is *yield-requested* (the holder is
told at its next heartbeat and in its brief); idle for `[lease].hold_max_s` it is
*breakable*: the claimant's claim records a `lease.expired` carrying `broken_by`, the
idle time and the contested files. Nothing is deleted -- the lease object keeps its
worktree and branch, the holder re-claims through the normal door -- and the break is a
logged event, never silent.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core import clock
from ..core.model import Lease, State
from ..infra import treeactivity as TA
from ..infra import worktree as W
from . import waiting as WG

#: Event kinds that only say a lease exists or is being asked for; none is work.
BOOKKEEPING = frozenset(
    {"lease.acquired", "lease.renewed", "lease.waiting", "lease.expired", "lease.released"}
)

#: `(lease) -> epoch seconds of the newest work in its tree`; 0.0 for none.
Activity = Callable[[Lease], float]


@dataclass(frozen=True)
class Verdict:
    """One contended lease and how long it has shown no work."""

    item: str
    holder: str
    idle_s: float
    waiters: tuple[str, ...] = field(default_factory=tuple)
    yield_requested: bool = False
    breakable: bool = False

    def line(self) -> str:
        who = ", ".join(self.waiters) or "a claimant"
        return (
            f"{self.item} ({self.holder}) has shown no progress for "
            f"{clock.fmt_age(self.idle_s)} while {who} waits for its files"
        )


def tree_activity(repo: Path, base: str = "") -> Activity:
    """The default `Activity`: the lease's worktree, read from git and the filesystem."""

    def activity(lease: Lease) -> float:
        if not lease.worktree:
            return 0.0
        tree = W.load_path(repo, lease.worktree)
        return TA.last_activity(tree, base or W.default_branch(repo), lease.branch)

    return activity


def progress_at(events: Iterable[Any], item_id: str, lease: Lease, activity: Activity) -> float:
    """The progress watermark of ``lease`` on ``item_id`` (epoch seconds)."""
    best = max(lease.acquired_at, activity(lease))
    for e in events:
        if e.subject == item_id and e.kind not in BOOKKEEPING:
            best = max(best, clock.epoch(e.ts))
    return best


def judge(
    cfg,
    events: Iterable[Any],
    item_id: str,
    lease: Lease,
    waiters: Iterable[str],
    now: float,
    activity: Activity,
) -> Verdict | None:
    """The verdict on a lease contended by ``waiters``, or None when nobody contends it or
    it is within its limits (or the limits are off)."""
    waiting = tuple(sorted(set(waiters)))
    idle_max, hold_max = cfg.lease.idle_max_s, cfg.lease.hold_max_s
    if not waiting or not (idle_max or hold_max):
        return None
    idle = now - progress_at(events, item_id, lease, activity)
    breakable = bool(hold_max) and idle >= hold_max
    asked = breakable or (bool(idle_max) and idle >= idle_max)
    if not asked:
        return None
    return Verdict(item_id, lease.holder, idle, waiting, True, breakable)


def contended(st: State, cfg, now: float) -> dict[str, list[str]]:
    """Leased item -> the items whose recorded waits name it (the wait-for graph, inverted)."""
    out: dict[str, list[str]] = {}
    for waiter, held in WG.wait_for_graph(st, cfg, now).items():
        for item_id in held:
            if item_id != waiter:
                out.setdefault(item_id, []).append(waiter)
    return out


def verdicts(st: State, cfg, events: list[Any], now: float, activity: Activity) -> list[Verdict]:
    """Every yield-requested or breakable lease standing now (recorded waits only), by idle time."""
    live = st.active_leases(now, cfg.lease.grace_s)
    out = []
    for item_id, waiters in contended(st, cfg, now).items():
        lease = live.get(item_id)
        v = lease and judge(cfg, events, item_id, lease, waiters, now, activity)
        if v:
            out.append(v)
    return sorted(out, key=lambda v: (-v.idle_s, v.item))


def breaking_record(
    lease: Lease, claimant: str, by: str, pair: Iterable[str], v: Verdict
) -> dict[str, Any]:
    """The payload of the `lease.expired` that breaks ``lease``: the ordinary expiry fields
    (so the fold, recovery and every reader treat it as an expiry) plus who, why and for what."""
    return {
        "holder": lease.holder,
        "event": lease.event,
        "worktree": lease.worktree,
        "branch": lease.branch,
        "reason": f"broken for {claimant}: no progress for {clock.fmt_age(v.idle_s)}",
        "broken_by": by,
        "broken_for": claimant,
        "idle_s": round(v.idle_s, 1),
        "files": list(pair),
    }


def broken_notices(
    events: Iterable[Any], agent: str, st: State | None = None
) -> list[dict[str, Any]]:
    """The leases of ``agent`` that were broken and not claimed back since: item, by, for,
    idle_s, branch, worktree, newest first. For the holder's heartbeat and brief."""
    out: dict[str, dict[str, Any]] = {}
    for e in events:
        d = e.data
        if e.kind == "lease.expired" and d.get("broken_by") and d.get("holder") == agent:
            out.pop(e.subject, None)  # a later break moves it to the end
            out[e.subject] = {
                "item": e.subject,
                "by": d["broken_by"],
                "for": d.get("broken_for", ""),
                "idle_s": d.get("idle_s", 0.0),
                "branch": d.get("branch", ""),
                "worktree": d.get("worktree", ""),
            }
        elif e.kind == "lease.acquired" and d.get("holder") == agent:
            out.pop(e.subject, None)  # claimed back
    if st is not None:
        out = {k: v for k, v in out.items() if k in st.items and not st.items[k].terminal}
    return list(reversed(out.values()))


def notice_text(n: dict[str, Any]) -> str:
    where = (
        f" Your branch {n['branch']} and worktree {n['worktree']} are kept." if n["branch"] else ""
    )
    return (
        f"your lease on {n['item']} was BROKEN by {n['by']} for {n['for'] or 'a waiting claim'}: "
        f"no progress for {clock.fmt_age(n['idle_s'])} while it waited for your files.{where} "
        f"`ddflow claim {n['item']}` takes it back once the files are free."
    )


def yield_text(v: Verdict) -> str:
    return (
        f"{', '.join(v.waiters)} waits for the files {v.item} holds and it has shown no "
        f"progress for {clock.fmt_age(v.idle_s)}: commit, narrow its globs, or `ddflow "
        f"release {v.item}`. " + ("It can now be broken (its work is kept)." if v.breakable else "")
    )


def break_hint(cfg) -> str:
    """Why `claim --break` was refused: the lease is not (yet) breakable."""
    return (
        f"\n\n--break applies only to a lease that is contended and has shown no progress "
        f"for [lease].hold_max_s ({cfg.lease.hold_max_s}s; 0 = never); this one is not "
        f"breakable now."
    )


@dataclass
class Prejudged:
    """What a claim read BEFORE it took the append lock, so judging idleness under the lock
    costs `stat` calls, not a read of the log or a `git` subprocess (DX-lease-break-lock-scope).

    ``mark`` is the log's fingerprint taken before ``events`` were read: while it still holds
    under the lock ``events`` are current; when it moved the claim re-reads (the lock-free
    fold's contract). ``seen`` is the newest worktree activity of each lease that clashed."""

    mark: Any = None
    events: list[Any] = field(default_factory=list)
    seen: dict[tuple[str, str, str], float] = field(default_factory=dict)

    @staticmethod
    def key(lease: Lease) -> tuple[str, str, str]:
        return (lease.holder, lease.event, lease.worktree)

    def activity(self, real: Activity) -> Activity:
        """``real``, answering from the pre-read watermarks where it has them."""
        return lambda lease: self.seen[k] if (k := self.key(lease)) in self.seen else real(lease)

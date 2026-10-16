"""What is wrong with the lanes, as a pure function of facts (DX-health-watch).

On 2026-10-15 an agent sat idle for an hour behind a lease its heartbeat loop kept
renewing, three agents deadlocked for 45 minutes, and a stopped agent was found by
reading timestamps by hand. This module is the inspection that would have said so: it
takes plain values -- one `Lane` per lease, one `Proc` per job, the standing waits and
the wait-for cycles -- and returns `Finding`s. No state, no clock, no I/O, so `doctor`,
`status`, `ddflow health` and the periodic pass read one answer.

Four conditions, each with evidence and the command that corrects it:

* STALLED  a current lease whose holder shows no progress for ``stall_s`` (a heartbeat is
           not progress: the watermark is the newest event, commit or file change).
* LOCKED   a lane in a cycle of the wait-for graph, or waiting on a stalled lane's lease.
* CRASHED  a lease that ran out (or was recorded expired) over a worktree that holds work,
           or one that could not be measured: the work is at risk, never the lane's fault.
* RUNAWAY  a job still running after ``runaway_s``.

Nothing here decides to act; `services.healthwatch` turns findings into corrections.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import clock

STALLED, LOCKED, CRASHED, RUNAWAY = "stalled", "locked", "crashed", "runaway"
KINDS = (CRASHED, LOCKED, STALLED, RUNAWAY)


@dataclass(frozen=True)
class Lane:
    """One lease and what is known of its holder's progress."""

    item: str
    holder: str
    live: bool  # inside its TTL and not recorded expired
    progress_at: float  # epoch seconds of the newest work (never a renewal)
    #: True = the worktree holds work, False = measured clean, None = could not measure
    work: bool | None = False
    #: an expiry is already recorded in the log (the lane was marked recoverable)
    recorded: bool = False
    worktree: str = ""


@dataclass(frozen=True)
class Proc:
    """One job that has not ended."""

    id: str
    item: str
    started_at: float
    running: bool


@dataclass(frozen=True)
class Wait:
    """``agent`` waits on ``item`` behind the leases ``on`` (``(item, holder)`` pairs)."""

    item: str
    agent: str
    on: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Limits:
    stall_s: float = 3600
    runaway_s: float = 14400


@dataclass(frozen=True)
class Finding:
    kind: str
    item: str
    holder: str
    age_s: float
    detail: str
    fix: str
    #: extra structured evidence a correction reads (a cycle, the waiters, a job id)
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.kind, self.item, self.holder)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "item": self.item,
            "holder": self.holder,
            "age_s": round(self.age_s, 1),
            "detail": self.detail,
            "fix": self.fix,
            "evidence": self.evidence,
        }

    def render(self) -> str:
        return f"{self.kind.upper()} {self.item} ({self.holder}): {self.detail}. Fix: {self.fix}"


def _stalled(lane: Lane, waiters: list[str], age: float) -> Finding:
    if waiters:
        fix = (
            f"`ddflow claim {waiters[0]} --break` (its work is kept), or ask {lane.holder} to yield"
        )
        why = f"{', '.join(waiters)} waits for its files"
    else:
        fix = f"nudge {lane.holder}; nothing waits for it, so nothing is taken"
        why = "nothing waits for it"
    return Finding(
        STALLED,
        lane.item,
        lane.holder,
        age,
        f"lease is current but no progress for {_age(age)}; {why}",
        fix,
        {"waiters": waiters},
    )


def _age(s: float) -> str:
    return clock.fmt_age(s)


def _crashed(ln: Lane, now: float) -> Finding:
    where = f" in {ln.worktree}" if ln.worktree else ""
    held = "unmeasurable work" if ln.work is None else "unmerged or uncommitted work"
    return Finding(
        CRASHED,
        ln.item,
        ln.holder,
        now - ln.progress_at,
        f"lease expired with {held}{where}"
        + ("; recorded expired, awaiting recovery" if ln.recorded else ""),
        f"`ddflow recover` shows the work; `ddflow claim {ln.item}` resumes it",
        {"worktree": ln.worktree, "recorded": ln.recorded, "work": ln.work},
    )


def _cycle_locks(cycles: list[dict[str, Any]], seen: set[tuple[str, str]]) -> list[Finding]:
    out = []
    for c in cycles:
        loop = " -> ".join([*c["agents"], c["agents"][0]])
        for k in c["links"]:
            if (k["item"], k["agent"]) in seen:
                continue
            seen.add((k["item"], k["agent"]))
            out.append(
                Finding(
                    LOCKED,
                    k["item"],
                    k["agent"],
                    0.0,
                    f"in a lease deadlock {loop} over {', '.join(c['files']) or 'its files'}",
                    c["remedy"] or "`ddflow doctor` names the edge that ends it",
                    {"cycle": c["agents"], "via": "cycle"},
                )
            )
    return out


def _stalled_locks(
    waits: list[Wait],
    by_item: dict[str, Lane],
    stalled: dict[str, Finding],
    seen: set[tuple[str, str]],
    now: float,
) -> list[Finding]:
    out = []
    for w in waits:
        behind = [b for b, _h in w.on if b in stalled and b != w.item]
        if (w.item, w.agent) in seen or not behind:
            continue
        seen.add((w.item, w.agent))
        lane = by_item[behind[0]]
        out.append(
            Finding(
                LOCKED,
                w.item,
                w.agent,
                now - lane.progress_at,
                f"waits on {behind[0]}, held by {lane.holder}, which has made no progress "
                f"for {_age(now - lane.progress_at)}",
                stalled[behind[0]].fix,
                {"behind": behind, "via": "stalled"},
            )
        )
    return out


def _runaways(procs: list[Proc], now: float, limit: float) -> list[Finding]:
    return [
        Finding(
            RUNAWAY,
            p.item,
            p.id,
            now - p.started_at,
            f"job {p.id} has run for {_age(now - p.started_at)}",
            f"`ddflow job list` shows it; end it yourself with `ddflow job end {p.id}` "
            "once you have looked (the watchdog never signals a process)",
            {"job": p.id},
        )
        for p in procs
        if limit > 0 and p.running and now - p.started_at >= limit
    ]


def inspect(
    lanes: list[Lane],
    procs: list[Proc],
    waits: list[Wait],
    cycles: list[dict[str, Any]],
    now: float,
    limits: Limits,
) -> list[Finding]:
    """Every finding for these facts, ordered CRASHED, LOCKED, STALLED, RUNAWAY, then by item.

    ``cycles`` are `leasewait.Cycle.as_dict()` rows. A lane is judged on its own facts: a
    lane in a cycle is LOCKED even when it is also stalled (both are reported)."""
    out: list[Finding] = []
    by_item = {ln.item: ln for ln in lanes}
    waiting_for: dict[str, list[str]] = {}
    for w in waits:
        for blocker, _h in w.on:
            if blocker != w.item:
                waiting_for.setdefault(blocker, []).append(w.item)
    stalled: dict[str, Finding] = {}
    for ln in lanes:
        age = now - ln.progress_at
        if ln.live and limits.stall_s > 0 and age >= limits.stall_s:
            stalled[ln.item] = _stalled(ln, sorted(set(waiting_for.get(ln.item, []))), age)
        if not ln.live and ln.work is not False:
            out.append(_crashed(ln, now))
    seen: set[tuple[str, str]] = set()
    out += stalled.values()
    out += _cycle_locks(cycles, seen)
    out += _stalled_locks(waits, by_item, stalled, seen, now)
    out += _runaways(procs, now, limits.runaway_s)
    return sorted(out, key=lambda f: (KINDS.index(f.kind), f.item, f.holder))

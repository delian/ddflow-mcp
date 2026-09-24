"""Dependency resolution, readiness and parallel fan-out.

The scheduler answers exactly one question: *given the current state, which items may
start RIGHT NOW, and for each item that may not, why not?* The "why not" half is not a
nicety — an agent told only "nothing to do" will invent work, whereas an agent told
"T3 waits on T2, which agent-b holds until 14:05" waits correctly or picks another
phase.

Two independent reasons block an item, and they are reported separately because the
remedies differ:

* **Dependency** — ``needs`` names an item that is not done. Remedy: wait, or do that.
* **Conflict** — another agent holds a live lease whose file globs overlap ours.
  Remedy: pick a non-overlapping item; the whole point of reporting the non-overlapping
  ones is that re-ordering beats blocking.

Glob overlap is deliberately over-eager. A false positive costs one unnecessary
re-order; a false negative costs two agents editing one file and one of them silently
losing their work. The asymmetry is not close, so the comparison errs toward "yes".
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from fnmatch import fnmatch

from .config import Config
from .model import ABANDONED, DONE, RUNNING, Item, Lease, State


@dataclass
class Blocked:
    item: str
    reason: str  # "deps" | "conflict" | "state" | "cycle"
    detail: str = ""
    waiting_on: list[str] = field(default_factory=list)


@dataclass
class Plan:
    """What the scheduler decided, and everything it decided against."""

    ready: list[Item] = field(default_factory=list)
    blocked: list[Blocked] = field(default_factory=list)
    running: list[Item] = field(default_factory=list)
    cycles: list[list[str]] = field(default_factory=list)

    def summary(self) -> str:
        parts = [
            f"{len(self.ready)} ready",
            f"{len(self.running)} running",
            f"{len(self.blocked)} blocked",
        ]
        if self.cycles:
            parts.append(f"{len(self.cycles)} CYCLE(S)")
        return ", ".join(parts)


def globs_overlap(a: str, b: str) -> bool:
    """Do two path patterns plausibly cover a common file?

    Exact match, either pattern matching the other as a literal, or a shared literal
    prefix. Deliberately approximate: computing true regular-language intersection of
    two globs is both hard and beside the point, since the answer only decides whether
    to re-order.
    """
    if a == b:
        return True
    if fnmatch(a, b) or fnmatch(b, a):
        return True
    pa = a.split("*", maxsplit=1)[0].split("?", maxsplit=1)[0]
    pb = b.split("*", maxsplit=1)[0].split("?", maxsplit=1)[0]
    if not pa or not pb:
        return True  # a bare "*" covers everything
    return pa.startswith(pb) or pb.startswith(pa)


def conflicts(mine: list[str], theirs: list[str]) -> list[tuple[str, str]]:
    return [(a, b) for a in mine for b in theirs if globs_overlap(a, b)]


def find_cycles(items: dict[str, Item]) -> list[list[str]]:
    """Every dependency cycle, each reported once, starting at its smallest id.

    Iterative DFS. Recursion depth here is the length of the dependency chain, which is
    operator-authored and therefore unbounded by anything the code controls; a plan with
    a thousand-deep chain would raise RecursionError inside the health check that exists
    to diagnose bad plans.
    """
    WHITE, GREY, BLACK = 0, 1, 2
    colour: dict[str, int] = {}
    found: list[list[str]] = []

    for root in sorted(items):
        if colour.get(root, WHITE) != WHITE:
            continue
        stack: list[tuple[str, list[str]]] = [(root, list(items[root].needs))]
        path: list[str] = [root]
        colour[root] = GREY
        while stack:
            _node, pending = stack[-1]
            if not pending:
                stack.pop()
                colour[path.pop()] = BLACK
                continue
            dep = pending.pop()
            if dep not in items:
                continue
            c = colour.get(dep, WHITE)
            if c == GREY:
                cyc = path[path.index(dep) :]
                lo = cyc.index(min(cyc))
                found.append(cyc[lo:] + cyc[:lo] + [cyc[lo]])
            elif c == WHITE:
                colour[dep] = GREY
                path.append(dep)
                stack.append((dep, list(items[dep].needs)))
    uniq = {tuple(c): c for c in found}
    return sorted(uniq.values())


def dep_status(state: State, dep: str, cfg: Config) -> tuple[bool, str]:
    """Is one dependency satisfied? Returns (satisfied, explanation).

    An UNKNOWN id is unmet by default (``schedule.unknown_dep_policy``), so a typo in
    ``needs`` surfaces as a blocked item rather than as an item that silently starts
    early. Treating unknown as satisfied is the vacuous-truth trap in its purest form.
    """
    it = state.items.get(dep)
    if it is None or it.removed:
        if cfg.schedule.unknown_dep_policy == "block":
            return False, f"unknown dependency {dep!r} (typo? or not yet added)"
        return True, f"unknown dependency {dep!r} ignored by policy"
    if it.state == DONE:
        return True, ""
    if it.kind == "phase":
        kids = [t for t in state.tasks(it.id) if t.state != DONE]
        if not kids and it.state != DONE:
            return False, f"phase {dep} has no open tasks but is not marked done"
        return False, f"phase {dep} has {len(kids)} open task(s)"
    return False, f"{dep} is {it.state}"


def _is_umbrella(state: State, it: Item) -> bool:
    """Does this item have work beneath it?

    An umbrella is not something to claim — its children are. Offering it would give an
    agent a worktree for an item whose actual work lives in three other items, and the
    umbrella cannot complete until they do anyway.
    """
    return bool(state.open_descendants(it.id))


def item_blocker(
    state: State,
    cfg: Config,
    it: Item,
    live: dict[str, Lease],
    *,
    agent: str,
    now: float,
    in_cycle: set[str],
    cycles: list[list[str]],
) -> Blocked | None:
    """Why can this item not start? ``None`` means it can.

    The single readiness predicate for the whole package. It exists because three
    places once decided this independently and the copies disagreed: an agent refused
    one item was offered an alternative the scheduler would also refuse.
    """
    if _is_umbrella(state, it):
        kids = state.open_descendants(it.id)
        return Blocked(
            it.id,
            "umbrella",
            f"has {len(kids)} unfinished sub-task(s): "
            f"{', '.join(k.id for k in kids[:6])}. Work those; this closes when they do.",
            [k.id for k in kids],
        )
    if it.id in in_cycle and cfg.schedule.cycle_policy == "error":
        cyc = next(c for c in cycles if it.id in c)
        return Blocked(it.id, "cycle", " -> ".join(cyc), [])

    unmet: list[str] = []
    details: list[str] = []
    for dep in it.needs:
        ok, why = dep_status(state, dep, cfg)
        if not ok:
            unmet.append(dep)
            details.append(why)
    if unmet:
        return Blocked(it.id, "deps", "; ".join(details), unmet)

    if cfg.schedule.ready_policy != "deps_and_lease":
        return None

    held = live.get(it.id)
    if held and held.holder != agent:
        return Blocked(
            it.id,
            "conflict",
            f"leased by {held.holder} for another {held.remaining_s(now):.0f}s",
            [it.id],
        )
    for other_id, lease in live.items():
        if other_id == it.id or lease.holder == agent:
            continue
        pairs = conflicts(it.globs, lease.globs)
        if pairs:
            return Blocked(
                it.id,
                "conflict",
                f"globs overlap {other_id} held by {lease.holder} ({pairs[0][0]} vs {pairs[0][1]})",
                [other_id],
            )
    return None


def plan(
    state: State,
    cfg: Config,
    *,
    kind: str = "task",
    phase: str = "",
    now: float | None = None,
    agent: str = "",
) -> Plan:
    """Compute the ready set.

    ``phase`` restricts to one phase's tasks -- the "implement phase X" entry point.
    ``agent`` is the caller's identity: an item this agent already holds counts as
    running-by-me, not as a conflict against me.
    """
    now = time.time() if now is None else now
    p = Plan()
    grace = cfg.lease.grace_s

    if kind == "task":
        # `state.tasks(phase)` walks DESCENDANTS, so sub-tasks nested below a task are
        # candidates too. Filtering on direct parentage excluded them entirely: a task
        # split into two left both halves unreachable, and the queue looked empty while
        # holding the only work there was.
        candidates = state.tasks(phase)
    else:
        candidates = [
            i
            for i in state.items.values()
            if i.kind == kind and not i.removed and (not phase or phase in (i.parent, i.id))
        ]
    p.cycles = find_cycles({i.id: i for i in state.items.values() if not i.removed})
    in_cycle = {n for c in p.cycles for n in c}
    live = state.active_leases(now, grace)

    for it in sorted(candidates, key=lambda x: (x.priority, x.id)):
        if it.state in (DONE, ABANDONED):
            continue
        if it.state == RUNNING and it.id in live:
            # Running and leased. Always reported as running; never offered as ready,
            # INCLUDING to the agent that holds it -- offering it would put the same
            # item in two lists, and a caller iterating `ready` would start it twice.
            p.running.append(it)
            continue
        blocker = item_blocker(
            state,
            cfg,
            it,
            live,
            agent=agent,
            now=now,
            in_cycle=in_cycle,
            cycles=p.cycles,
        )
        if blocker is not None:
            p.blocked.append(blocker)
        else:
            p.ready.append(it)

    cap = min(cfg.schedule.max_parallel_tasks, cfg.worktree.max_parallel)
    slots = max(0, cap - len(p.running))
    if len(p.ready) > slots:
        for it in p.ready[slots:]:
            p.blocked.append(
                Blocked(
                    it.id,
                    "state",
                    f"parallelism cap reached ({cap}); {len(p.running)} running",
                    [],
                )
            )
        p.ready = p.ready[:slots]
    return p


def critical_path(state: State, phase: str = "") -> list[str]:
    """Longest dependency chain among not-done items — the true wall-clock floor.

    Reporting it is what stops an operator adding a fifth parallel agent to a phase
    whose runtime is set by a four-deep chain: total time equals the longest path, not
    the sum of the work.
    """
    items = {
        i.id: i
        for i in state.items.values()
        if not i.removed and (not phase or phase in (i.parent, i.id))
    }
    # On a cyclic graph the memo is unsound: a result computed under one `seen` set is
    # keyed on the node alone, so a truncated sub-path can be cached and returned where
    # it is wrong. Cycles are reachable via `cycle_policy = "warn"`, so refuse rather
    # than return a confidently wrong number.
    if find_cycles(items):
        return []
    memo: dict[str, list[str]] = {}

    def longest(n: str, seen: frozenset[str]) -> list[str]:
        if n in seen:
            return []
        if n in memo:
            return memo[n]
        it = items.get(n)
        best: list[str] = []
        if it:
            for dep in it.needs:
                if dep in items and items[dep].state != DONE:
                    cand = longest(dep, seen | {n})
                    if len(cand) > len(best):
                        best = cand
        memo[n] = [*best, n]
        return memo[n]

    chains = [longest(i, frozenset()) for i, it in items.items() if it.state != DONE]
    return max(chains, key=len) if chains else []

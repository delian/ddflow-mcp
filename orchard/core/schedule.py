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

from ..config import Config
from ..core.model import ABANDONED, BLOCKED, DONE, RUNNING, Item, Lease, State


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
    #: Items the log says are RUNNING with nobody holding them — see `interrupted`.
    #: Offered as ready (someone must resume them) but never silently.
    interrupted: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [
            f"{len(self.ready)} ready",
            f"{len(self.running)} running",
            f"{len(self.blocked)} blocked",
        ]
        if self.cycles:
            parts.append(f"{len(self.cycles)} CYCLE(S)")
        if self.interrupted:
            parts.append(f"{len(self.interrupted)} INTERRUPTED")
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


def inherited_deps(state: State, it: Item) -> list[tuple[str, str]]:
    """Every dependency that binds ``it``: its own, plus every ancestor's.

    Returns ``(owner_id, dep_id)`` pairs so a refusal can say WHERE the dependency
    came from — told only "P2.T1 needs P1", an operator goes looking for a
    declaration that is not in P2.T1 and concludes the tool is confused.

    **Why inheritance is the whole point.** A phase is never claimed; only its tasks
    are. So a readiness rule reading ``it.needs`` and stopping there makes every
    phase-level dependency decorative: ``P2 needs P1`` blocks P2, which no agent was
    going to pick up, and permits every task inside P2, which is what an agent
    actually starts. The same hole appeared one level down once sub-tasks existed —
    an umbrella declaring ``needs A, B`` had children with empty ``needs``, so they
    were handed out while A and B were still open.

    The one dependency that must NOT be inherited is one pointing into ``it``'s own
    subtree. An umbrella that declares a dependency on its own child would otherwise
    make that child wait for itself, and a plan typo would become a permanent hang.
    Beneath an umbrella, such a dependency is satisfied by running, not by waiting;
    the umbrella still carries it, and the umbrella cannot close early anyway.
    """
    pairs: list[tuple[str, str]] = [(it.id, d) for d in it.needs]
    ancestors = state.ancestors(it.id)
    if not ancestors:
        return pairs
    mine = state.descendants(it.id) | {it.id}
    for anc in ancestors:
        pairs += [(anc.id, d) for d in anc.needs if d not in mine]
    seen: set[tuple[str, str]] = set()
    return [p for p in pairs if not (p in seen or seen.add(p))]


def _is_umbrella(state: State, it: Item) -> bool:
    """Does this item have work beneath it?

    An umbrella is not something to claim — its children are. Offering it would give an
    agent a worktree for an item whose actual work lives in three other items, and the
    umbrella cannot complete until they do anyway.
    """
    return bool(state.open_descendants(it.id))


def plan_blocker(
    state: State,
    cfg: Config,
    it: Item,
    *,
    in_cycle: set[str] | None = None,
    cycles: list[list[str]] | None = None,
) -> Blocked | None:
    """Why the PLAN forbids starting this item — umbrella, cycle or dependency.

    Split out from :func:`item_blocker` because two layers need exactly this much and
    no more. The scheduler adds the lease/conflict questions on top; the lease layer
    asks them itself, unconditionally, because refusing an overlapping claim is a
    safety property rather than a scheduling preference and must not be switchable
    off by ``schedule.ready_policy``.

    Sharing it is not tidiness. ``claim`` used to check leases and globs but *no*
    dependencies at all, so `next` would report "T2: deps — T1 is open" and `claim T2`
    would hand out a worktree one second later. An agent that picks work by id rather
    than by asking `next` therefore bypassed the entire dependency graph — the one
    guarantee the queue exists to provide.
    """
    if in_cycle is None or cycles is None:
        cycles = find_cycles({i.id: i for i in state.items.values() if not i.removed})
        in_cycle = {n for c in cycles for n in c}
    if it.state == BLOCKED:
        return Blocked(
            it.id,
            "state",
            it.blocked_reason or "parked by an operator",
            [],
        )
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
    for owner, dep in inherited_deps(state, it):
        ok, why = dep_status(state, dep, cfg)
        if not ok:
            unmet.append(dep)
            details.append(why if owner == it.id else f"{why} (inherited from {owner})")
    if unmet:
        return Blocked(it.id, "deps", "; ".join(details), unmet)
    return None


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
    blocked = plan_blocker(state, cfg, it, in_cycle=in_cycle, cycles=cycles)
    if blocked is not None:
        return blocked

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


def interrupted(state: State, it: Item, live: dict[str, Lease]) -> str:
    """Why this item looks mid-flight with nobody on it. ``""`` when it does not.

    RUNNING with no live lease means the agent died between starting and claiming, or
    released without finishing. `lease.scan` has always called that `stale_running` and
    told an operator to recover it; `plan()` handed the same item to the next agent as
    ordinary ready work, with no mention that someone had been there. Both behaviours
    are defensible. Deciding them in two modules that never consult each other is not —
    the second agent starts from scratch on work that may exist, uncommitted, in a
    worktree nobody mentioned.

    So the classification lives here, once, and both callers ask it. The scheduler does
    NOT refuse the item — someone has to resume it, and refusing would strand it — it
    annotates the offer. `lease.scan` measures the worktree, which is I/O and stays in
    the service layer; this says only what the log says.
    """
    if it.state != RUNNING or it.id in live:
        return ""
    where = f" in {it.worktree}" if it.worktree else ""
    return (
        f"{it.id} is RUNNING with no live lease{where}: an agent started it and did not "
        f"finish. Run `orchard recover --item {it.id}` before starting from scratch — "
        f"its worktree may hold uncommitted work."
    )


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
            note = interrupted(state, it, live)
            if note:
                p.interrupted.append(note)
            p.ready.append(it)

    # TWO caps, because they are two different statements and `min()` of them was one
    # number pretending to be one statement:
    #
    #   schedule.max_parallel_tasks — how many items may be IN FLIGHT at once. A
    #     `--no-worktree` lease (a review, a research task) is in flight, so it counts.
    #   worktree.max_parallel       — how many worktrees may EXIST at once. That is a
    #     claim about this machine's disk and CPU, and a lease that never made a tree
    #     consumes neither, so it does not count against it.
    #
    # Under the old `min()` a queue allowed four in flight silently became one on a
    # machine allowed one worktree, whatever the leases were actually doing.
    #
    # What this does NOT do, precisely because it cannot: apply the tree cap per item.
    # `--no-worktree` is a flag on `claim`, not a field on `Item`, so at planning time
    # nothing distinguishes a task that will take a tree from one that will not, and
    # `slots` is necessarily one number for all of them. A full tree cap therefore
    # still withholds a task that would have taken no tree. The counting is right and
    # the granularity is not; making it right needs an item-level declaration, which is
    # filed rather than guessed (docs/BACKLOG.md, B52). Raised as THEORETICAL by the
    # cross-family critic on 2026-09-24 and confirmed as a granularity gap, not a
    # counting bug: the new form is a strict relaxation of the old one in every case.
    #
    # Counted across the WHOLE queue, not the slice this call asked about: `p.running`
    # holds only candidates from `phase`, so with `--phase` the cap was applied against
    # a count of ~0 and two agents each asking about their own phase were both told to
    # go ahead.
    live_items = [i for i in live if i in state.items and not state.items[i].removed]
    in_flight = len(live_items)
    with_trees = len([i for i in live_items if live[i].worktree])
    flight_slots = max(0, cfg.schedule.max_parallel_tasks - in_flight)
    if cfg.worktree.enabled:
        tree_slots = max(0, cfg.worktree.max_parallel - with_trees)
    else:
        tree_slots = flight_slots  # no trees are made, so no tree cap applies
    slots = min(flight_slots, tree_slots)
    if slots == flight_slots:
        why = (
            f"parallelism cap reached (schedule.max_parallel_tasks="
            f"{cfg.schedule.max_parallel_tasks}); {in_flight} in flight across the queue"
        )
    else:
        why = (
            f"worktree cap reached (worktree.max_parallel={cfg.worktree.max_parallel}); "
            f"{with_trees} worktrees live across the queue"
        )
    if len(p.ready) > slots:
        for it in p.ready[slots:]:
            p.blocked.append(Blocked(it.id, "state", why, []))
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
            # INHERITED dependencies, like readiness uses. The path used to walk
            # `it.needs` alone, so a phase-level dependency did not lengthen the
            # reported floor at all — and the number exists precisely to stop someone
            # adding a fifth agent to a phase whose runtime is set by a chain.
            for _owner, dep in inherited_deps(state, it):
                if dep in items and items[dep].state != DONE:
                    cand = longest(dep, seen | {n})
                    if len(cand) > len(best):
                        best = cand
        memo[n] = [*best, n]
        return memo[n]

    chains = [longest(i, frozenset()) for i, it in items.items() if it.state != DONE]
    return max(chains, key=len) if chains else []

"""The ready-set planner: `plan()` and the conflict-aware slot cutter."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence

from ...adapters.optimizer import Candidate, Slots, get_optimizer
from ...config import Config
from .. import flowcontrol as FC
from ..admission import glob_conflict
from ..graph import find_cycles
from ..model import REVIEW, RUNNING, Item, State
from ._filters import PlanCtx, item_blocker, order_terms
from ._parallel import _cap_words, parallel_line
from ._readiness import Blocked, Plan, unpickable
from ._reserved import caps, open_slots, reserved_note
from ._resources import bug_items, interrupted, needs_tree

#: What a plan names as holding work back when a slot is free but reserved.
RESERVED = "schedule.reserved_slots"


def _cut_ready(
    state: State,
    cfg: Config,
    p: Plan,
    slots: int,
    free: Slots,
    reserved: frozenset[str],
    live_items: list[str],
    live_note: str,
    reached: str,
    hold: Callable[[Item], Blocked | None] | None,
) -> None:
    """Trim ``p.ready`` to the free slots, in place, never offering two overlapping items."""
    # Cut the ready list conflict-aware, by the configured optimizer: it walks the offer order
    # and takes an item only when it overlaps nothing already taken (two offered items that
    # overlap make the second claim refuse). An overlapping item is not dropped -- it stays
    # queued, blocked with the offered item it waits behind.
    by_id = {it.id: it for it in p.ready}
    cands = [
        Candidate(it.id, (), tuple(it.globs), tuple(it.tags), needs_tree(it), it.id in reserved)
        for it in p.ready
    ]
    holds: dict[str, Blocked] = {}
    clashes: dict[str, object] = {}

    def held_fn(c: Candidate) -> bool:
        held = hold(by_id[c.id]) if hold else None
        if held is not None:
            holds[c.id] = held
        return held is not None

    def clash_fn(c: Candidate, taken: Sequence[Candidate]) -> str:
        it = by_id[c.id]
        clash = glob_conflict(
            state, cfg, it, list(it.globs), against="offered", others=[by_id[t.id] for t in taken]
        )
        if not clash:
            return ""
        clashes[c.id] = clash
        return clash.item

    result = get_optimizer(cfg.schedule.optimizer).cut(
        {}, cands, free, hold=held_fn, clash=clash_fn
    )
    taken: list[Item] = []
    cut: list[Item] = []
    kept_out: list[Item] = []
    for v in result.verdicts:
        it = by_id[v.id]
        if v.what == "held":
            p.blocked.append(holds[v.id])
        elif v.what == "clash":
            pair = clashes[v.id].pair  # type: ignore[attr-defined]
            p.blocked.append(
                Blocked(
                    it.id,
                    "conflict",
                    f"globs overlap {v.other} ({pair[0]} vs {pair[1]}) offered in this plan",
                    [v.other],
                )
            )
            p.overlapped.append(it.id)
        elif v.what == "taken":
            taken.append(it)
        elif v.what == "reserved":
            kept_out.append(it)
        else:
            cut.append(it)
    for it in kept_out:
        # A slot IS free, so never "cap reached" (B40386f7a40): it waits for an ordinary one.
        why = f"held by {RESERVED}: {p.reserved_note}; {live_note}"
        p.blocked.append(Blocked(it.id, "state", why, sorted(live_items)))
        p.capped.append(it.id)
    if cut:
        # `claim` never applies the cap to a NAMED item (an explicit `ddflow claim <id>` is
        # granted whatever the cap, pinned by test_a_cap_blocked_item_wait_agrees_with_claim): the
        # cap trims this offered READY SET only. Say so, or a lane cannot tell a wait for a slot
        # from an item it could claim right now (B30f9d383ba).
        named = "a named `ddflow claim` of it is not capped and grants it now"
        if slots:
            # NOT "cap reached": a slot is free, and a higher-ranked item is offered it.
            # Said as "reached", an agent waiting for the message to clear waited hours
            # on an item `claim` would have granted at once (B40386f7a40).
            names = ", ".join(i.id for i in taken)
            why = (
                f"held by {p.cap_note}: {live_note}; {slots} slot(s) free, offered to "
                f"higher-ranked {names}; {named}"
            )
        else:
            # `_blocking_leases` (api.lifecycle) keys on "cap reached".
            why = f"{reached}; {live_note}; {named}"
        # Any release frees a slot, so a cap-held item waits on every item in flight --
        # named here, so that a waiter registers against all of them whatever the wording
        # (the free-slot wording no longer says "cap reached").
        holders = sorted(live_items)
        for it in cut:
            p.blocked.append(Blocked(it.id, "state", why, holders))
            p.capped.append(it.id)
    if kept_out:
        # Only the cut items are the cap's: with none, the cap held nothing back.
        p.cap_note = f"{p.cap_note} and {RESERVED}" if cut else RESERVED
    p.ready = taken


def _ordered(ctx: PlanCtx, candidates: list[Item]) -> list[Item]:
    """The candidates in the configured optimizer's order."""
    terms = order_terms(ctx)
    by_id = {i.id: i for i in candidates}
    cands = [Candidate(i.id, terms(i)) for i in candidates]
    return [by_id[x.id] for x in get_optimizer(ctx.cfg.schedule.optimizer).order({}, cands)]


def plan(
    state: State,
    cfg: Config,
    *,
    kind: str = "task",
    phase: str = "",
    now: float | None = None,
    agent: str = "",
    hold: Callable[[Item], Blocked | None] | None = None,
    parallel: FC.Decision | None = None,
) -> Plan:
    """Compute the ready set.

    ``hold`` lets a caller withhold an item the pure state cannot see is spoken for (a
    waiter in line, `claim` would refuse it): a Blocked it returns goes to ``blocked`` and
    the item neither takes a slot nor counts against the offered items' overlap check.

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
    p.cycles = find_cycles(state.live_by_id())
    in_cycle = {n for c in p.cycles for n in c}
    live = state.active_leases(now, grace)

    bugs = frozenset(bug_items(state, cfg)) if cfg.schedule.bugs_first else frozenset()
    ctx = PlanCtx(state, cfg, agent, now, live, in_cycle, p.cycles, bugs)
    for it in _ordered(ctx, candidates):
        if it.terminal:
            continue
        if it.state == REVIEW:
            p.review.append(it)
            continue
        if it.state == RUNNING and it.id in live:
            # Running and leased. Always reported as running; never offered as ready,
            # INCLUDING to the agent that holds it -- offering it would put the same
            # item in two lists, and a caller iterating `ready` would start it twice.
            p.running.append(it)
            continue
        if it.merged_sha:
            # B-merged-uncompleted-claimable: a merged item not yet completed is not fresh
            # work. `merge` released the lease and `complete` needs no lease, so `next` must
            # not offer it -- a second lane's claim of it would run `item.reworked` and clear
            # the merging lane's gates before it completes. The claim door refuses that
            # re-claim itself (`api.lifecycle.claim`), the merger's own re-claim reopens.
            p.blocked.append(
                Blocked(
                    it.id,
                    "state",
                    f"merged ({it.merged_sha[:12]}) but not completed; `ddflow complete "
                    f"{it.id}` records it. Re-claim as the merged lane (or `--force`) to "
                    f"rework it.",
                    [],
                )
            )
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
                # An INTERRUPTED item (RUNNING, no live lease) is nobody's work right now:
                # `ddflow recover` resumes it, `claim` refuses it. It is still in flight, so
                # it is reported under `running`, never offered a free ready slot -- a slot it
                # took hid CLAIMABLE work behind "held by the cap" with no one to run the
                # slot (Bcec3b3229f).
                p.running.append(it)
                continue
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
    # The tree cap is applied PER ITEM: an item tagged `no-worktree` (a review, a research
    # task) declares it takes no tree, so a full tree cap does not withhold it and `claim`
    # makes it none (B52). An untagged item is assumed to take a tree.
    #
    # Counted across the WHOLE queue, not the slice this call asked about: `p.running`
    # holds only candidates from `phase`, so with `--phase` the cap was applied against
    # a count of ~0 and two agents each asking about their own phase were both told to
    # go ahead.
    c = caps(state, cfg, live, parallel)
    live_items, in_flight, with_trees, limit = c.live_items, c.in_flight, c.with_trees, c.limit
    flight_slots, tree_slots = c.flight_slots, c.tree_slots
    slots = min(flight_slots, tree_slots)
    if slots == flight_slots:
        cap = _cap_words(cfg, parallel, limit)
        live_note = f"{in_flight} in flight across the queue"
        p.cap_note = f"the parallelism cap ({cap})"
        reached = f"parallelism cap reached ({cap})"
        tree_binds = False
    else:
        cap = f"worktree.max_parallel={cfg.worktree.max_parallel}"
        live_note = f"{with_trees} worktrees live across the queue"
        p.cap_note = f"the worktree cap ({cap})"
        reached = f"worktree cap reached ({cap})"
        tree_binds = True
    p.reserved_note = reserved_note(cfg, limit, tree_binds=tree_binds)
    free, reserved = open_slots(state, cfg, live, live_items, limit, flight_slots, tree_slots)
    _cut_ready(state, cfg, p, slots, free, reserved, live_items, live_note, reached, hold)
    if parallel is not None:
        line = parallel_line(cfg, parallel, limit, in_flight, len(p.ready), p.capped)
        p.parallel_line = "; ".join(filter(None, [line, p.reserved_note]))
    # Asked of `unpickable`, the one rule `doctor` reports by; offered, never acted on.
    p.finished = [
        u.item
        for u in unpickable(state, cfg)
        if u.kind == "finished_phase"
        and (not phase or u.item == phase or any(a.id == phase for a in state.ancestors(u.item)))
    ]
    return p

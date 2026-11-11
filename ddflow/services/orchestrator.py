"""The orchestrator's native wave loop: what a free lane claims next, and what to do
next at all (`fill_order`, then `monitor`).

The orchestration wave loop answers one steering question each wave: *what do I claim
next?* `ddflow next` already says which items may start; what was left to prose (the
orchestrator's prompt told the loop to recover-and-force, then bugs, then the rest) is
now code. `fill_order` is that policy as a pure, testable function, so the fill order a
coordination loop follows is one derivation, not a comment that drifts:

  1. ``recover``  -- an interrupted/orphaned item (RUNNING with no live lease):
     `ddflow recover --item <id> --plan` then `claim --force`, so a crash's uncommitted
     work is salvaged before anything new starts from scratch;
  2. ``bug``      -- an open-bug fix (bugs-first);
  3. ``unphased`` -- a task under no phase;
  4. ``ready``    -- the remaining ready items, by priority then id.

The schedule applies the same order through its ordering keys
(``core.schedule.order_key``), so `ddflow next` already offers items in this order;
`fill_order` reuses those keys rather than re-deriving them (one policy, two views) and
accepts the candidates the caller already has.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..adapters.clock import DEFAULT as _CLOCK
from ..config import Config
from ..core.graph import find_cycles
from ..core.model import Item, State
from ..core.schedule import PlanCtx, bug_items, interrupted, order_key, plan

#: The fill tiers, highest-value first (their proxies: recover, bug, unphased, ready).
FILL_ORDER: tuple[str, ...] = ("recover", "bug", "unphased", "ready")


def _ctx(
    cfg: Config,
    state: State,
    *,
    agent: str,
    now: float | None,
    live: dict,
    bugs: frozenset[str] | None,
    in_cycle: set[str] | None,
    cycles,
):
    """A schedule ``PlanCtx`` for ``fill_order``, derived only when the caller held none.

    The ORDER it ranks by is `core.schedule.plan`'s own keys, so this module agrees with
    `ddflow next`; ``now`` reads the clock port (`ddflow.adapters.clock`), the schedule
    layer's own source of "now". A caller that already folded a plan passes the pieces it
    read rather than deriving them a second time."""
    now = _CLOCK.wall() if now is None else now
    live = state.active_leases(now, cfg.lease.grace_s) if live is None else live
    cycles = list(find_cycles(state.live_by_id())) if cycles is None else cycles
    in_cycle = {n for c in cycles for n in c} if in_cycle is None else in_cycle
    if bugs is None:
        bugs = frozenset(bug_items(state, cfg)) if cfg.schedule.bugs_first else frozenset()
    return PlanCtx(state, cfg, agent, now, live, in_cycle, cycles, bugs)


def fill_order(
    cfg: Config,
    state: State,
    items,
    *,
    agent: str = "",
    now: float | None = None,
    live: dict | None = None,
    bugs: frozenset[str] | None = None,
    in_cycle: set[str] | None = None,
    cycles=None,
) -> list[Item]:
    """``items`` in fill order (recover, bug, unphased, ready). Stable: equal-ranked items
    keep their input order, so the caller's own tiebreak inside a tier is preserved."""
    ctx = _ctx(
        cfg,
        state,
        agent=agent or cfg.agent.id,
        now=now,
        live=live,
        bugs=bugs,
        in_cycle=in_cycle,
        cycles=cycles,
    )
    return sorted(items, key=order_key(ctx))


@dataclass(frozen=True)
class Act:
    """One action the native coordinator should take next, in value order.

    ``verb`` is ``recover`` (resume an INTERRUPTED item: salvage its work before
    anything starts from scratch), ``complete`` (record an item that merged but was
    never completed) or ``fill`` (claim the next ready item). ``item`` is the item id
    and ``why`` says what the action is for, worded so a caller can print it as-is."""

    verb: str
    item: str
    why: str = ""


def monitor(
    cfg: Config,
    state: State,
    *,
    agent: str = "",
    now: float | None = None,
    live: dict | None = None,
    bugs: frozenset[str] | None = None,
    in_cycle: set[str] | None = None,
    cycles=None,
) -> list[Act]:
    """The native coordinator's next actions (`Act`), most valuable first.

    `fill_order` answers *which ready item to claim next*; this is its loop
    counterpart, *what to do next at all*. It MONITORS the implement status the log
    holds and derives the ACTIONS that resume the loop, once, in code — the monitor
    the harness cron used to substitute for:

      1. ``recover``  -- an INTERRUPTED item (RUNNING with no live lease): `ddflow
         recover --item <id>` resumes it, so a crash's uncommitted work is salvaged
         before anything new starts from scratch;
      2. ``complete`` -- an item that merged but was never completed: `ddflow complete
         <id>` records it, releasing what its open state still holds;
      3. ``fill``     -- a ready item in `fill_order` order, one claim each.

    Refill — how many lanes exist, and when the loop's next wave runs — stays the
    harness wave-driver's call (decision D-autofill-is-harness-pattern): this is a
    pure derivation of the action list, with no daemon and no timer of its own, so any
    trigger (a cron line, `/implement`, a session start) drives the same loop
    (decision D-sched-no-daemon-r2)."""
    now = _CLOCK.wall() if now is None else now
    agent = agent or cfg.agent.id
    live = state.active_leases(now, cfg.lease.grace_s) if live is None else live
    recover: list[Item] = []
    complete: list[Item] = []
    for iid, it in state.items.items():
        if it.merged_sha and not it.terminal:
            # A merged item is landed-but-unrecorded, never INTERRUPTED: recover is for
            # salvaging a crash's uncommitted work, and a merged item has none left (its
            # work is already in the base). It is `complete`, exactly once.
            complete.append(it)
        elif interrupted(state, it, live):
            recover.append(it)
    acts: list[Act] = []
    for it in fill_order(
        cfg, state, recover, agent=agent, now=now, live=live, bugs=bugs, in_cycle=in_cycle, cycles=cycles
    ):
        acts.append(Act("recover", it.id, interrupted(state, it, live)))
    for it in fill_order(
        cfg, state, complete, agent=agent, now=now, live=live, bugs=bugs, in_cycle=in_cycle, cycles=cycles
    ):
        acts.append(Act("complete", it.id, f"merged ({it.merged_sha[:12]}) but not completed"))
    ready = plan(state, cfg, agent=agent, now=now).ready
    for it in fill_order(
        cfg,
        state,
        ready,
        agent=agent,
        now=now,
        live=live,
        bugs=bugs,
        in_cycle=in_cycle,
        cycles=cycles,
    ):
        acts.append(Act("fill", it.id))
    return acts

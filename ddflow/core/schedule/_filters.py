"""Readiness filters and ordering keys, registered at import time.

A readiness filter answers "why can this item not start?" — a `Blocked` carrying the
reason it is passed over — or ``None``. Registered by name, run in registration order,
the first answer wins.

`item_blocker` is the single readiness predicate for the whole package: the registered
`FILTERS`, in order, the first answer wins. It exists because three places once decided
this independently and the copies disagreed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ...config import Config
from ..admission import glob_conflict
from ..model import Item, Lease, State
from ._readiness import Blocked, plan_blocker
from ._resources import capacities, interrupted, resource_shortfall


@dataclass
class PlanCtx:
    """Everything a readiness filter or an ordering key may look at, computed once per
    plan: the state, the caller, the clock, the live leases and the dependency cycles."""

    state: State
    cfg: Config
    agent: str
    now: float
    live: dict[str, Lease]
    in_cycle: set[str]
    cycles: list[list[str]]
    bugs: frozenset[str] = frozenset()
    #: The globs a caller says its claim will name (`wait --globs`, `claim --globs`); None means
    #: judge the item's own stored globs. The `globs` filter must honour this, or `wait
    #: --item --globs` reports a clash on a glob the caller left out (B-wait-globs-narrowing-ignored).
    globs: list[str] | None = None


#: A readiness filter answers "why can this item not start?" -- a `Blocked` carrying the
#: reason it is passed over -- or ``None``. Registered by name, run in registration order,
#: the first answer wins.
Filter = Callable[[PlanCtx, Item], Blocked | None]
FILTERS: dict[str, Filter] = {}
#: Filters that only apply under `[schedule] ready_policy = "deps_and_lease"`: the ones
#: that read the lease table. Under `deps_only` an item's dependencies alone decide.
LEASE_FILTERS: set[str] = set()
#: An ordering key ranks an item (smaller first). The plan sorts by the keys named in
#: `ORDER`, left to right; `register_key` adds one at a position.
Key = Callable[[PlanCtx, Item], object]
KEYS: dict[str, Key] = {}
#: The fill order of the orchestrator's next-task selection (B4e2a112a97): recover an
#: interrupted item first, then bugs, then a task under no phase, then the rest. Each
#: name maps to a key registered below; `register_key` inserts at a position.
ORDER: list[str] = ["recover_first", "bugs_first", "unphased_first", "priority", "id"]


def register_filter(name: str, fn: Filter, *, lease: bool = False) -> None:
    """Add (or replace) the readiness filter ``name``. A new name runs last."""
    FILTERS[name] = fn
    if lease:
        LEASE_FILTERS.add(name)


def register_key(name: str, fn: Key, *, before: str = "id") -> None:
    """Add (or replace) the ordering key ``name``; a new one goes before ``before`` -- by
    default ahead of the id tiebreak, which always ends the order."""
    KEYS[name] = fn
    if name not in ORDER:
        ORDER.insert(ORDER.index(before) if before in ORDER else len(ORDER) - 1, name)


def order_key(ctx: PlanCtx) -> Callable[[Item], tuple]:
    """The sort key the plan walks candidates by: the registered keys, in order."""
    return lambda it: tuple(KEYS[n](ctx, it) for n in ORDER)


def order_terms(ctx: PlanCtx) -> Callable[[Item], tuple[tuple[str, object], ...]]:
    """The same keys as `order_key`, itemised as ``(name, value)`` for the optimizer port."""
    return lambda it: tuple((n, KEYS[n](ctx, it)) for n in ORDER)


def _f_deps(ctx: PlanCtx, it: Item) -> Blocked | None:
    return plan_blocker(ctx.state, ctx.cfg, it, in_cycle=ctx.in_cycle, cycles=ctx.cycles)


def _f_lease(ctx: PlanCtx, it: Item) -> Blocked | None:
    held = ctx.live.get(it.id)
    if held and held.holder != ctx.agent:
        left = held.left_text(ctx.now, ctx.cfg.lease.grace_s, unit="another")
        # Inside the grace the countdown is a clause of its own ("leased by a; ran out ...").
        sep = " " if left.startswith("for another") else "; "
        return Blocked(
            it.id,
            "conflict",
            f"leased by {held.holder}{sep}{left}",
            [it.id],
        )
    return None


def _f_globs(ctx: PlanCtx, it: Item) -> Blocked | None:
    clash = glob_conflict(
        ctx.state,
        ctx.cfg,
        it,
        list(it.globs) if ctx.globs is None else list(ctx.globs),
        ctx.agent,
        against="live",
        live=ctx.live,
    )
    if clash is None:
        return None
    return Blocked(
        it.id,
        "conflict",
        f"globs overlap {clash.item} held by {clash.lease.holder if clash.lease else ''} "
        f"({clash.pair[0]} vs {clash.pair[1]})",
        [clash.item],
    )


def _f_resources(ctx: PlanCtx, it: Item) -> Blocked | None:
    if it.resources and it.id not in ctx.live:
        try:
            short = resource_shortfall(it.resources, ctx.live, capacities(ctx.cfg), exclude=it.id)
        except ValueError as exc:
            short = str(exc)
        if short:
            return Blocked(it.id, "resources", short, [])
    return None


register_filter("deps", _f_deps)
register_filter("lease", _f_lease, lease=True)
register_filter("globs", _f_globs, lease=True)
register_filter("resources", _f_resources, lease=True)


def _interrupted(ctx: PlanCtx, it: Item) -> bool:
    """True for an item mid-flight with nobody on it: recover it before anything new."""
    return interrupted(ctx.state, it, ctx.live) != ""


def _unphased(ctx: PlanCtx, it: Item) -> bool:
    """True for a task under no phase (a sub-task whose root was removed, or filed none)."""
    return it.kind == "task" and not any(a.kind == "phase" for a in ctx.state.ancestors(it.id))


register_key("recover_first", lambda ctx, it: not _interrupted(ctx, it))
register_key("bugs_first", lambda ctx, it: it.id not in ctx.bugs)
register_key("unphased_first", lambda ctx, it: not _unphased(ctx, it))
register_key("priority", lambda ctx, it: it.priority)
register_key("id", lambda ctx, it: it.id)


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
    globs: list[str] | None = None,
) -> Blocked | None:
    """Why can this item not start? ``None`` means it can.

    The single readiness predicate for the whole package: the registered `FILTERS`, in
    order, the first answer wins. It exists because three places once decided this
    independently and the copies disagreed: an agent refused one item was offered an
    alternative the scheduler would also refuse.
    """
    ctx = PlanCtx(state, cfg, agent, now, live, in_cycle, cycles, globs=globs)
    lease_policy = cfg.schedule.ready_policy == "deps_and_lease"
    for name, fn in FILTERS.items():
        if name in LEASE_FILTERS and not lease_policy:
            continue
        blocked = fn(ctx, it)
        if blocked is not None:
            return blocked
    return None

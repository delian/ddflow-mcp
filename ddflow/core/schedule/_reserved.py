"""Reserved parallelism slots: `[schedule].reserved_slots` (DX-reserved-slot).

Ordinary work may hold at most ``cap - reserved`` slots of a cap. That one sentence is the
whole rule: reserved work (a bug fix, a task tagged `[schedule].reserved_tags`) may take any
free slot, so it fills the reserved share first and overflows into the ordinary one, and the
cap itself already bounds the sum. A reserved slot with no such work stays free.

It is the offer that holds work back, as with the cap (`claim` applies neither to an item it
is asked for by name). One slot of every cap always stays ordinary: a reservation covering a
whole limit, which the adaptive limit can reach on its own, would strand every feature.
"""

from __future__ import annotations

from collections.abc import Iterable

from ...adapters.optimizer import Slots
from ...config import Config
from ..model import Lease, State
from ._resources import bug_items


def tree_cap(cfg: Config, limit: int) -> int:
    """How many worktrees may exist: 0 (the default) FOLLOWS the schedule limit; it is not
    unlimited (B-af-config)."""
    return cfg.worktree.max_parallel or limit


def reserved_items(state: State, cfg: Config) -> frozenset[str]:
    """Ids of the items that may take a reserved slot: every bug fix, whatever
    `bugs_first` says about the order, and every task tagged `reserved_tags`."""
    tags = set(cfg.schedule.reserved_tags)
    tagged = {i.id for i in state.items.values() if tags.intersection(i.tags)}
    return frozenset(bug_items(state, cfg) | tagged)


def reserved_in_force(wanted: int, cap: int) -> int:
    """How many of ``wanted`` slots a cap of ``cap`` reserves: all but its last one."""
    return max(0, min(wanted, cap - 1))


def ordinary_free(cap: int, wanted: int, used: int) -> int:
    """Slots ordinary work may still take, with ``used`` of them taken by ordinary work."""
    return max(0, cap - reserved_in_force(wanted, cap) - used)


def open_slots(
    state: State,
    cfg: Config,
    live: dict[str, Lease],
    live_items: list[str],
    limit: int,
    flight: int,
    tree: int,
) -> tuple[Slots, frozenset[str]]:
    """What the cut may hand out, and the ids that may use the reserved share."""
    wanted = cfg.schedule.reserved_slots
    if not wanted:
        return Slots(flight, tree), frozenset()
    reserved = reserved_items(state, cfg)
    ordinary = [i for i in live_items if i not in reserved]
    share = ordinary_free(limit, wanted, len(ordinary))
    tree_share = share
    if cfg.worktree.enabled:
        with_trees = len([i for i in ordinary if live[i].worktree])
        tree_share = ordinary_free(tree_cap(cfg, limit), wanted, with_trees)
    return Slots(flight, tree, min(share, flight), min(tree_share, tree)), reserved


def idle_reserved(state: State, cfg: Config, in_flight: Iterable[str], limit: int) -> int:
    """Reserved slots sitting idle while ordinary work has used its whole share of ``limit``.

    The adaptive limit counts them as in use: it grows only while in flight reaches it, and
    an idle reservation would otherwise hold in flight under the limit for good, with the
    features it keeps out exactly what a higher limit would admit. 0 with ordinary slots
    still open, where the limit holds nothing back, and never more than the slots free: a
    claim by name may take a reserved slot, as `claim` applies neither the cap nor this."""
    wanted = cfg.schedule.reserved_slots
    if not wanted:
        return 0
    flying, reserved = set(in_flight), reserved_items(state, cfg)
    if ordinary_free(limit, wanted, len(flying - reserved)):
        return 0
    unused = reserved_in_force(wanted, limit) - len(flying & reserved)
    return max(0, min(unused, limit - len(flying)))


def reserved_note(cfg: Config, limit: int) -> str:
    """The one phrase `next`, `status` and `brief` print; "" when nothing is reserved."""
    wanted = cfg.schedule.reserved_slots
    if not wanted:
        return ""
    tags = ", ".join(cfg.schedule.reserved_tags)
    what = f"bug fixes and tags {tags}" if tags else "bug fixes"
    force = reserved_in_force(wanted, limit)
    note = f"{force} of {limit} slot(s) reserved for {what}"
    return note if force == wanted else f"{note} ({wanted} set; one always stays ordinary)"

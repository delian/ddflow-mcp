"""The dispatcher's plan: which items to start RIGHT NOW, on which lane, in what order.

Pure (DX-dispatch-plan). No I/O, no clock, no model call and no write: everything arrives
as arguments, so the same inputs give the same answer and a plan can be replayed. Two
pieces:

* `fill` / `plan` -- the greedy fill of the free slots: the ready items in start order
  (bug fixes first, then priority), each placed on the best eligible lane, consuming the
  lane's cap and marking the item live, so two items that overlap live globs are never
  offered together. Advise only: it returns a list of `Start` rows, nothing starts.
* `steer` -- the lanes to RESUME, YIELD or NUDGE, from the existing lane advisor
  (`services.lanes.advise`), mapped to the dispatch vocabulary.

The scheduler core it builds on is `services.swarm.score` and `services.swarm.dispatch`
(the pure planner of SW-pull); the read side is `ddflow.api.lifecycle.ready` (`ddflow
next`) for the ready set and `ddflow.services.lanes` for the fleet.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from ..lanes import advise as AD
from ..swarm import score
from ..swarm.score_config import Weights

#: The steer verbs in urgency order: a stopped lane first, then a stall, then a lane that
#: is one past the limit. The dispatch output re-labels the advisor's own kinds.
RESUME = "RESUME"
YIELD = "YIELD"
NUDGE = "NUDGE"


@dataclass(frozen=True)
class Start:
    """One item to start now: the lane (agent) to run it and how the caller starts it."""

    item: str
    lane: str
    globs: tuple[str, ...] = ()
    note: str = ""  # why this lane for this item (the reasons the score carried)
    claim: str = ""  # the claim command the caller runs, e.g. `ddflow claim <item>`


@dataclass(frozen=True)
class Move:
    """A lane to steer, as the dispatch reads it: RESUME, YIELD or NUDGE."""

    verb: str
    lane: str
    text: str = ""


@dataclass(frozen=True)
class Plan:
    """The dispatch plan: what to start now and the lanes to steer."""

    starts: tuple[Start, ...]
    moves: tuple[Move, ...]
    free: int = 0  # free slots the fill consumed
    sequence: tuple[str, ...] = ()  # the item ids in the order they must start


def sort_ready(
    items: Sequence[score.Item],
    *,
    bugs: Mapping[str, object] | Sequence[str] = (),
    priorities: Mapping[str, int] | None = None,
) -> list[score.Item]:
    """Ready items in start order: bug fixes first, then priority (high first), then id.

    The order is the planner's promise (`a bug fix outranks a feature`): a standing bug
    gets fixed before more features are built on it, and inside a class priority orders
    them, ties broken by id so the order is stable and repeatable.
    """
    bug_set = set(bugs)
    prio = dict(priorities or {})
    return sorted(items, key=lambda it: (it.id not in bug_set, -(prio.get(it.id, 0)), it.id))


def fill(
    ready: Sequence[score.Item],
    lanes: Sequence[score.Lane],
    world: score.World,
    free: int,
    w: Weights,
) -> tuple[list[Start], score.World]:
    """Greedily fill up to ``free`` slots from ``ready`` over ``lanes`` in `sort_ready`
    order, an item at a time. The ``world`` returned is the caller's with each accepted
    item added as live -- so a second `fill` over it never re-offers an overlapping item.

    Each accepted item takes the best eligible lane `score.choose` returns; the lane's cap
    is consumed (its `live` is raised), and the item joins ``world.live`` so an item whose
    globs overlap it is refused from any later slot. An item no lane accepts is skipped,
    not dead-ending the fill: the slots after it are still offered to the rest.
    """
    free = max(0, int(free))
    live: list[score.Live] = list(world.live)
    pool: list[score.Lane] = list(lanes)
    starts: list[Start] = []
    for _ in range(free):
        accepted = False
        for idx, item in enumerate(ready):
            decision = score.choose(item, pool, replace(world, live=tuple(live)), w)
            if decision is None:
                continue
            pool = [
                replace(ln, live=ln.live + 1) if ln.name == decision.lane else ln
                for ln in pool
            ]
            live.append(score.Live(item.id, item.globs, decision.family))
            starts.append(
                Start(
                    item=item.id,
                    lane=decision.lane,
                    globs=item.globs,
                    note="; ".join(decision.reasons),
                )
            )
            ready = ready[:idx] + ready[idx + 1 :]
            accepted = True
            break
        if not accepted:
            break
    return starts, replace(world, live=tuple(live))


def plan(
    ready: Sequence[score.Item],
    lanes: Sequence[score.Lane],
    world: score.World,
    free: int,
    w: Weights,
    *,
    bugs: Mapping[str, object] | Sequence[str] = (),
    priorities: Mapping[str, int] | None = None,
) -> Plan:
    """The ordered items to start now and the free slots they take, pure.

    ``free`` is how many slots are open (the parallel cap and reserved slots already
    applied by the caller); ``lanes`` are the fleet's scored lanes (cap already counted);
    ``world`` the scheduler snapshot of live leases. The order is `sort_ready`; the
    accepted set never holds two items whose globs overlap. `moves` is empty here -- steer
    is a separate read the caller composes in.
    """
    ordered = sort_ready(ready, bugs=bugs, priorities=priorities)
    starts, _ = fill(ordered, lanes, world, free, w)
    return Plan(
        starts=tuple(starts),
        moves=(),
        free=free,
        sequence=tuple(s.item for s in starts),
    )


#: The dispatch verb for an advisor move kind: a stall nudges its holder, an over-limit
#: lane yields, everything else inside the advisor's own name is kept (resume stays
#: resume). ``idle`` is spare capacity, not a steer, and is dropped by `steer`.
_VERB = {
    "resume": RESUME,
    "stall": NUDGE,
    "over-limit": YIELD,
}


def steer(moves: Sequence[AD.Move]) -> tuple[Move, ...]:
    """The advisor's moves (`services.lanes.advise.advise`) as dispatch `Move`s: a resume
    stays RESUME, a stall becomes NUDGE, an over-limit lane YIELDs. The idle move -- spare
    capacity, not a lane to correct -- is dropped, since it names lanes to START, not ones
    to steer.
    """
    return tuple(
        Move(_VERB[m.kind], m.lanes[0] if m.lanes else "", m.text)
        for m in moves
        if m.kind in _VERB
    )

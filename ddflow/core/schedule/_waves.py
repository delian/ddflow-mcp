"""Dependency waves: the earliest wave each item can start in, for a whole plan."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TypeVar

from ..globs import overlap as globs_overlap
from ..graph import find_cycles

#: An item of a PLAN, as `waves` sees it: whatever type the caller's `edges`/`globs_of` read.
T = TypeVar("T")


def waves(
    items: Mapping[str, T],
    *,
    edges: Callable[[T], list[str]],
    globs_of: Callable[[T], list[str]],
    cap: int | None = None,
) -> dict[str, int]:
    """The earliest WAVE each item can start in (1 = no dependencies), for a whole plan.

    A wave is a set that can run TOGETHER, which is what a plan needs and readiness alone
    cannot say: every item's dependencies lie in earlier waves, no two items in one wave
    have overlapping globs (the overlap `claim` refuses a lease for), and no wave holds
    more than ``cap`` items. A pair that could both start but not together is therefore
    re-ordered rather than blocked -- the same trade the conflict report makes.

    Items on a dependency cycle get NO wave: they can never start, and `find_cycles` names
    them (`plan check` reports that separately). Pure: it reads the plan it is given and
    nothing else. For the same reason a dependency naming an item OUTSIDE ``items`` counts
    as satisfied (wave 0) -- this judges the plan's own items, and the unknown id is the
    check's to report, not this function's to invent a blocker for.

    ``edges`` and ``globs_of`` say how to read an item, as `find_cycles`' own ``edges``
    does: the caller knows whether its items are `Item` records, a plan's dicts, or
    something else.
    """
    if cap is not None and cap < 1:
        raise ValueError(f"cap must be at least 1, or None for no cap, not {cap!r}")
    # `find_cycles` CONSUMES the list `edges` hands it (it pops as the iterative DFS walks),
    # so it is given a COPY here: an `edges` that returns the item's own `needs` would
    # otherwise come back emptied, and every wave below would read 1. (Its own docstring
    # insists a caller pass the graph it will walk -- walking one costs it.)
    on_cycle = {i for cycle in find_cycles(items, lambda it: list(edges(it))) for i in cycle}
    placed: dict[str, int] = {}
    in_wave: dict[int, list[str]] = {}

    def place(iid: str) -> None:
        deps = [d for d in edges(items[iid]) if d in items]
        w = 1 + max((placed[d] for d in deps), default=0)
        mine = list(globs_of(items[iid]))
        while True:
            here = in_wave.get(w, [])
            if cap is not None and len(here) >= cap:
                w += 1
                continue
            if any(globs_overlap(g, h) for j in here for g in mine for h in globs_of(items[j])):
                w += 1
                continue
            break
        placed[iid] = w
        in_wave.setdefault(w, []).append(iid)

    # Sorted, so the answer does not depend on the order the mapping happens to hold; and
    # repeated passes, because an item whose dependency comes LATER in that order must wait
    # for it rather than be placed as though it were free.
    pending = [i for i in sorted(items) if i not in on_cycle]
    while pending:
        waiting: list[str] = []
        for iid in pending:
            deps = [d for d in edges(items[iid]) if d in items]
            if any(d not in placed for d in deps):
                waiting.append(iid)
                continue
            place(iid)
        if len(waiting) == len(pending):
            break  # what is left waits on a cycle: it can never start either
        pending = waiting
    return placed

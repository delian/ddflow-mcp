"""Tallies: how far along a phase or the queue is, counted once."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from ..model import ABANDONED, BLOCKED, DONE, OPEN, REVIEW, RUNNING, Item, State

#: Task states in the order a per-state count lists them.
STATE_ORDER: tuple[str, ...] = (DONE, RUNNING, REVIEW, OPEN, BLOCKED, ABANDONED)


@dataclass(frozen=True)
class Tally:
    """``done`` of ``live`` tasks finished; ``abandoned`` settled outside the total."""

    done: int = 0
    live: int = 0
    abandoned: int = 0

    @property
    def complete(self) -> bool:
        """Every live task is done, and there is at least one."""
        return self.live > 0 and self.done == self.live


def tally(tasks: Iterable[Item]) -> Tally:
    """The tally of these tasks, under the one rule above."""
    done = live = abandoned = 0
    for t in tasks:
        if t.state == ABANDONED:
            abandoned += 1
            continue
        live += 1
        done += t.state == DONE
    return Tally(done, live, abandoned)


def phase_tally(st: State, phase: str) -> Tally:
    """How far along ``phase`` is: its live tasks at every depth."""
    return tally(st.tasks(phase))


def phase_of(st: State, item_id: str) -> str:
    """The phase ``item_id`` belongs to: itself when it is one, else its nearest phase
    ancestor (a sub-task's PARENT is a task, not its phase: B45b55be6a4); "" for none."""
    it = st.items.get(item_id)
    if it is None:
        return ""
    if it.kind == "phase":
        return it.id
    return next((a.id for a in st.ancestors(item_id) if a.kind == "phase"), "")


def state_counts(tasks: Iterable[Item]) -> dict[str, int]:
    """How many of ``tasks`` are in each state, every state of ``STATE_ORDER`` present
    (zero included) and in that order; a state outside it follows, by name."""
    seen = Counter(t.state for t in tasks)
    out = {s: seen.pop(s, 0) for s in STATE_ORDER}
    out.update(sorted(seen.items()))
    return out

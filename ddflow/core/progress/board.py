"""Board rows: the board's structure, built once for the markdown and the JSON."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..graph import closure
from ..model import Item, State
from .tallies import Tally, tally


def depth(st: State, item: Item, root: str) -> int:
    """How far below ``root`` this item sits. Bounded, because a parent chain is
    operator-authored and a cycle in it must not hang the renderer."""

    def parent(i: str) -> list[str]:
        up = (item if i == item.id else st.items[i]).parent
        return [up] if up and up != root and up in st.items else []

    above = [n for n in closure(item.id, parent) if n != item.id]
    # A chain that ends in an item that is its own parent counts that last step once.
    last = st.items[above[-1]] if above else item
    return min(len(above) + (last.parent == last.id != root), 6)


def nested(st: State, phase: str, tasks: list | None = None) -> list:
    """Tasks under a phase, each sub-task immediately after its parent. ``tasks``
    overrides the set (the unphased ones, whose root is the empty id)."""
    if tasks is None:
        tasks = st.tasks(phase)
    by_parent: dict[str, list] = {}
    for t in tasks:
        by_parent.setdefault(t.parent, []).append(t)
    out: list = []

    def walk(parent: str, seen: set) -> None:
        for t in sorted(by_parent.get(parent, []), key=lambda x: (x.priority, x.id)):
            if t.id in seen:
                continue
            seen.add(t.id)
            out.append(t)
            walk(t.id, seen)

    walk(phase, set())
    # Anything whose parent chain does not reach the phase (an orphan, or a cycle)
    # still belongs on the board: silently dropping it is how work disappears.
    listed = {t.id for t in out}
    out.extend(t for t in tasks if t.id not in listed)
    return out


def unphased(st: State) -> list:
    """Live tasks that sit under no phase, a sub-task after its parent (Bc896ea5d16: the
    board listed only phases' tasks, so a task added with no phase never appeared)."""
    under: set[str] = set()
    for ph in st.phases():
        under |= set(st.descendants(ph.id))
    return nested(st, "", [t for t in st.tasks() if t.id not in under])


@dataclass(frozen=True)
class BoardSection:
    """One section of the board: a phase (``phase`` None for the unphased tasks), its
    tally, and its task rows in board order."""

    phase: Item | None
    tally: Tally
    rows: list[dict[str, Any]]


def board_rows(
    st: State, pipeline: Callable[[Item], list[str]], phase: str = ""
) -> list[BoardSection]:
    """The board's sections, in board order: each phase (only ``phase`` when one is
    named) by (priority, id), then the unphased tasks when no phase is named and there
    are any. ``pipeline(item)`` is the item's configured gate pipeline (core does not
    read the config: the caller hands it ``services.gates.pipeline_for``)."""

    def rows(tasks: list, root: str) -> list[dict[str, Any]]:
        return [
            {
                "id": t.id,
                "title": t.title,
                "state": t.state,
                "parent": t.parent,
                "depth": depth(st, t, root),
                "needs": list(t.needs),
                "globs": list(t.globs),
                "owner": t.lease.holder if t.lease else "",
                "gates": {g: t.gate_outcome(g) for g in pipeline(t)},
            }
            for t in tasks
        ]

    out = [
        BoardSection(ph, tally(tasks), rows(tasks, ph.id))
        for ph in sorted(st.phases(), key=lambda p: (p.priority, p.id))
        if not phase or ph.id == phase
        for tasks in [nested(st, ph.id)]
    ]
    loose = [] if phase else unphased(st)
    if loose:
        out.append(BoardSection(None, tally(loose), rows(loose, "")))
    return out


def new_reports_line(item: str, count: int) -> str:
    """The one line heartbeat and `gate status` carry; "" when there is nothing new."""
    if not count:
        return ""
    s = "" if count == 1 else "s"
    return f"{count} new report{s} on {item} since you claimed -- `ddflow show {item}`"

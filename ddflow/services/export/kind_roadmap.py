"""Roadmap: phases in lanes Now / Next / Later with done/total, open tasks, Done as a count.

A phase is in NOW when any of its tasks is running or in review, NEXT when it has an open
task nothing blocks, LATER when every open task waits on something (a dependency of its
own or of an ancestor, or the blocked state), and DONE (a count only) when all its tasks
are. Abandoned tasks are not counted. Pure over the Query: built from the single-pass
children index, so a log of thousands of items renders in milliseconds.
"""

from __future__ import annotations

from typing import Any

from ...core.model import ABANDONED, BLOCKED, DONE, OPEN, REVIEW, RUNNING, Item, State
from ...core.schedule import inherited_deps
from . import registry
from .frame import one_line
from .query import Query

_LANE_LABELS = (
    ("now", "Now", "work in flight"),
    ("next", "Next", "ready to start"),
    ("later", "Later", "waiting on something"),
)
_LANES = tuple(k for k, _, _ in _LANE_LABELS)


def _met(state: State, dep: str) -> bool:
    """Is one dependency satisfied? Unknown or unobserved ids are NOT (the scheduler's
    default policy), so a typo shows as blocked rather than as work that may start."""
    it = state.items.get(dep)
    if it is None or it.removed:
        seen = state.external.get(dep)
        return bool(seen and seen.get("state") == DONE)
    if it.state == DONE:
        return True
    if it.kind == "phase":
        kids = [t for t in state.tasks(it.id) if t.state != ABANDONED]
        return bool(kids) and all(t.state == DONE for t in kids)
    return False


def waits_on(state: State, it: Item) -> list[str]:
    """The ids ``it`` is waiting for, sorted (own and inherited dependencies not met)."""
    return sorted({d for _, d in inherited_deps(state, it) if not _met(state, d)})


def _task_row(q: Query, t: Item) -> dict[str, Any]:
    if t.state in (RUNNING, REVIEW):
        status, waits = t.state, []
    else:
        waits = waits_on(q.state, t)
        status = BLOCKED if (t.state == BLOCKED or waits) else "ready"
    return {
        "id": t.id,
        "title": one_line(t.title, 110),
        "status": status,
        "waits_on": waits,
        "holder": t.lease.holder if t.lease and t.state == RUNNING else "",
    }


def _data(q: Query, f: registry.Filters) -> dict[str, Any]:
    lanes: dict[str, list[dict[str, Any]]] = {k: [] for k in _LANES}
    done = 0
    for p in q.phases():
        if f.phase and p.id != f.phase:
            continue
        tasks = [t for t in q.tasks_under(p.id) if t.state != ABANDONED]
        n_done = sum(1 for t in tasks if t.state == DONE)
        if tasks and n_done == len(tasks):
            done += 1
            continue
        rows = [_task_row(q, t) for t in tasks if t.state != DONE]
        # tasks_under is in item_key order (priority, id): the lane's own order is stable.
        if any(r["status"] in (RUNNING, REVIEW) for r in rows):
            lane = "now"
        elif any(r["status"] == "ready" for r in rows):
            lane = "next"
        else:
            lane = "later"
        lanes[lane].append(
            {
                "id": p.id,
                "title": one_line(p.title, 100),
                "done": n_done,
                "total": len(tasks),
                "tasks": rows,
                "waits_on": waits_on(q.state, p) if not rows else [],
            }
        )
    cut = f.limit or 0
    hidden = 0
    if cut:  # --limit bounds the phases listed per lane; the counts stay true
        for k in _LANES:
            hidden += max(0, len(lanes[k]) - cut)
            lanes[k] = lanes[k][:cut]
    return {
        "lanes": [
            {"label": label, "blurb": blurb, "phases": lanes[k]} for k, label, blurb in _LANE_LABELS
        ],
        "done_phases": done,
        "hidden_phases": hidden,
        "open_tasks": sum(1 for t in q.tasks() if t.state in (OPEN, BLOCKED, RUNNING, REVIEW)),
    }


registry.register(
    registry.DocKind(
        name="roadmap",
        default_target="ROADMAP.md",
        data=_data,
        update_mode=registry.WHOLE,
        filters=frozenset({"limit", "phase"}),
        title="Roadmap: phases in Now / Next / Later lanes with progress",
    )
)

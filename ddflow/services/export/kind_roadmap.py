"""Roadmap: phases in lanes Now / Next / Later with done/total, open tasks, Done as a count.

A phase is in NOW when any of its tasks is running or in review, NEXT when it has an open
task nothing blocks, LATER when every open task waits on something (a dependency of its
own or of an ancestor, or the blocked state), and DONE (a count only) when it is closed and
all its tasks are done. A phase with no open task -- none yet, or all done but the phase
not closed -- is NEXT (nothing waits on it: there is work, or the phase is there to close)
or LATER (it names a dependency not met). Whether one dependency is met is the scheduler's
own answer (`schedule.dep_status`, under the project's `[flow].stack` and
`[schedule].unknown_dep_policy`), so a phase counts as met when it is closed, as for `next`
(B58010c24b3). Per dependency only: what `next` judges across several (two review branches
to stack on) is not reflected here.
Abandoned tasks are not counted. Pure over the Query: built from the single-pass
children index, so a log of thousands of items renders in milliseconds.
"""

from __future__ import annotations

from typing import Any

from ...config import Config
from ...core.model import ABANDONED, BLOCKED, DONE, REVIEW, RUNNING, Item, State
from ...core.schedule import dep_status, inherited_deps
from . import registry
from .frame import one_line
from .query import ExportError, Query

_LANE_LABELS = (
    ("now", "Now", "work in flight"),
    ("next", "Next", "ready to start"),
    ("later", "Later", "waiting on something"),
)
_LANES = tuple(k for k, _, _ in _LANE_LABELS)


def waits_on(state: State, it: Item, cfg: Config) -> list[str]:
    """The ids ``it`` is waiting for, sorted (own and inherited dependencies not met)."""
    return sorted({d for _, d in inherited_deps(state, it) if not dep_status(state, d, cfg)[0]})


def _task_row(q: Query, t: Item, cfg: Config) -> dict[str, Any]:
    if t.state in (RUNNING, REVIEW):
        status, waits = t.state, []
    else:
        waits = waits_on(q.state, t, cfg)
        status = BLOCKED if (t.state == BLOCKED or waits) else "ready"
    return {
        "id": t.id,
        "title": one_line(t.title, 110),
        "status": status,
        "waits_on": waits,
        "holder": t.lease.holder if t.lease and t.state == RUNNING else "",
    }


def _data(q: Query, f: registry.Filters) -> dict[str, Any]:
    if f.phase and (q.item(f.phase) is None or q.item(f.phase).kind != "phase"):
        raise ExportError(f"--phase {f.phase!r}: no such phase", registry.EXIT_REFUSED)
    # The project's config when the Query has a repo; the shipped defaults otherwise.
    cfg = Config.load(q.repo) if q.repo is not None else Config()
    lanes: dict[str, list[dict[str, Any]]] = {k: [] for k in _LANES}
    done = 0
    open_tasks = 0
    for p in q.phases():
        if f.phase and p.id != f.phase:
            continue
        tasks = [t for t in q.tasks_under(p.id) if t.state != ABANDONED]
        n_done = sum(1 for t in tasks if t.state == DONE)
        if p.state == DONE and n_done == len(tasks):
            done += 1
            continue
        rows = [_task_row(q, t, cfg) for t in tasks if t.state != DONE]
        open_tasks += len(rows)
        own_waits = waits_on(q.state, p, cfg) if not rows else []
        # tasks_under is in item_key order (priority, id): the lane's own order is stable.
        if any(r["status"] in (RUNNING, REVIEW) for r in rows):
            lane = "now"
        elif any(r["status"] == "ready" for r in rows):
            lane = "next"
        elif not rows and not own_waits:
            lane = "next"  # an empty phase nothing waits on is not blocked
        else:
            lane = "later"
        lanes[lane].append(
            {
                "id": p.id,
                "title": one_line(p.title, 100),
                "done": n_done,
                "total": len(tasks),
                "tasks": rows,
                "waits_on": own_waits,
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
        "open_tasks": open_tasks,
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

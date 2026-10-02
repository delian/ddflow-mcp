"""Status: counts, agent-hours, in-flight items and open bugs, as one short page.

A compact "where are we" for a README badge row or a standup: totals and the work in
flight, no per-task listing (that is the roadmap). Agent-hours come from the log's own lease
tracking (``core.progress.work``), so they are the same number ``ddflow status`` reports.
"""

from __future__ import annotations

from typing import Any

from ...core import progress
from ...core.model import ABANDONED, BLOCKED, DONE, OPEN, REVIEW, RUNNING
from . import registry
from .frame import one_line
from .query import Query

_ORDER = (DONE, RUNNING, REVIEW, OPEN, BLOCKED, ABANDONED)


def _seconds(q: Query) -> float:
    """Total claim-to-release time. An attempt still open is counted to the NEWEST EVENT
    in the log, not to the clock (``Attempt.seconds`` would read ``time.time()`` and the
    same log would render different bytes every run)."""
    last = progress.epoch(q.events[-1].ts) if q.events else 0.0
    total = 0.0
    for w in progress.work(q.events, q.state).values():
        for a in w.attempts:
            if a.started_at:
                total += max(0.0, (a.ended_at or last) - a.started_at)
    return total


def _data(q: Query, f: registry.Filters) -> dict[str, Any]:
    tasks = q.tasks()
    phases = q.phases()
    counts = {s: sum(1 for t in tasks if t.state == s) for s in _ORDER}
    seconds = _seconds(q)
    live = [d for d in q.decisions() if getattr(d, "live", False)]
    in_flight = [
        {
            "id": t.id,
            "title": one_line(t.title, 100),
            "state": t.state,
            "holder": t.lease.holder if t.lease else "",
        }
        for t in tasks
        if t.state in (RUNNING, REVIEW)
    ]
    return {
        "phases_total": len(phases),
        "phases_done": sum(1 for p in phases if p.state == DONE),
        "tasks_total": len(tasks),
        "task_counts": [{"state": s, "n": counts[s]} for s in _ORDER if counts[s]],
        "tasks_done": counts[DONE],
        "agent_hours": round(seconds / 3600, 1),
        "in_flight": in_flight,
        "open_bugs": sum(1 for b in q.bugs() if b.open),
        "bugs_total": len(q.bugs()),
        "lessons": len(q.lessons()),
        "decisions": len(live),
        "as_of": (q.events[-1].ts[:10] if q.events else ""),
    }


registry.register(
    registry.DocKind(
        name="status",
        default_target="STATUS.md",
        data=_data,
        update_mode=registry.WHOLE,
        filters=frozenset(),
        title="Status: counts, agent-hours, in-flight work, open bugs",
    )
)

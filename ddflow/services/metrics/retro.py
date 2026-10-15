"""``ddflow metrics retro <id>``: the timeline of one item, and where its time went.

The entries are the item's own events (claim, each gate and review round, triage, CI, merge,
completion) plus the moment it became ready. The gaps between consecutive entries are what
the item spent waiting or working; the biggest one is named, which is the answer to "why
was this slow". Nothing is interpreted beyond naming the two entries a gap lies between.
"""

from __future__ import annotations

from collections.abc import Sequence
from itertools import pairwise

from ...core import progress as PR
from ...core.events import Event
from .facts import OUTCOMES, Facts

#: Event kinds that are a step in an item's life (heartbeats and renewals are not).
STEPS = frozenset(
    {
        "task.added",
        "lease.acquired",
        "lease.released",
        "lease.expired",
        "gate.started",
        "review.triaged",
        "ci.result",
        "worktree.created",
        "worktree.merged",
        "item.completed",
        "item.abandoned",
        "item.blocked",
        "item.unblocked",
        *(f"gate.{v}" for v in OUTCOMES),
    }
)


def _label(ev: Event, rounds: dict[str, int]) -> str:
    d = ev.data if isinstance(ev.data, dict) else {}
    gate = d.get("gate") if isinstance(d.get("gate"), str) else ""
    verdict = ev.kind.partition(".")[2]
    if ev.kind.startswith("gate.") and gate and verdict in OUTCOMES:
        if gate in PR.REVIEW_GATES and verdict != "skipped":
            rounds[gate] = rounds.get(gate, 0) + 1
            return f"{gate} round {rounds[gate]} {verdict}"
        return f"{gate} {verdict}"
    if ev.kind == "gate.started":
        return f"{gate} started"
    if ev.kind == "review.triaged" and gate:
        return f"{gate} finding triaged"
    return ev.kind


def timeline(events: Sequence[Event], facts: Facts, item: str) -> dict:
    """The item's entries in time order, its gaps, and the biggest gap named."""
    rows: list[dict] = []
    rounds: dict[str, int] = {}
    own = sorted(
        (e for e in events if e.subject == item and e.kind in STEPS),
        key=lambda e: (PR.epoch(e.ts), e.lamport),
    )
    for ev in own:
        at = PR.epoch(ev.ts)
        if at > 0:
            rows.append({"at": at, "what": _label(ev, rounds), "by": ev.agent})
    span = facts.spans.get(item)
    if rows and span is not None and span.ready_at is not None:  # readiness alone is no story
        rows.append({"at": span.ready_at, "what": "ready (dependencies done)", "by": ""})
        rows.sort(key=lambda r: r["at"])
    if not rows[1:]:
        return {"item": item, "entries": rows, "gaps": [], "biggest_gap": None, "total_s": None}
    gaps = [
        {
            "from": a["what"],
            "to": b["what"],
            "by": b["by"],
            "seconds": b["at"] - a["at"],
            "start": a["at"],
        }
        for a, b in pairwise(rows)
    ]
    for r in rows:
        r["offset_s"] = r["at"] - rows[0]["at"]
    biggest = max(gaps, key=lambda g: g["seconds"])
    return {
        "item": item,
        "entries": rows,
        "gaps": sorted(gaps, key=lambda g: -g["seconds"]),
        "biggest_gap": biggest,
        "total_s": rows[-1]["at"] - rows[0]["at"],
    }

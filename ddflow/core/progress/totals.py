"""Totals: the ONE place the queue's task, phase and bug totals are counted (B6341fe42ca).

`ddflow status` (prose and JSON), the MCP status tool, the `Progress:` block a completion
prints and the STATUS.md export each used to count for themselves, from different lists
and under different definitions, so the same queue read "12/5065", "2222" and "1571/4146"
on three surfaces. They all read :func:`queue_totals` now, and print :meth:`Totals.definition`
so a reader sees what each number counts.

The definition, stated on the screen::

    N of M live tasks done (K abandoned and F fix tasks listed separately)

and, beside it, every bucket of every task record, summing to the total (the buckets the
scheduler's plan puts each task in -- Bdcce70d036 -- so none is counted twice or not at
all). Phases and bugs are counted the same way: every record in exactly one bucket.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from ..model import ABANDONED, DONE, REVIEW, RUNNING, Item, State

#: The buckets of a task record, in print order, when a scheduler plan places the open ones.
PLANNED = ("done", "running", "ready", "held_by_cap", "blocked", "review", "abandoned")
#: Without a plan (a pure function of the log: the STATUS.md export) the open tasks cannot be
#: split into ready / held by a cap / blocked, so they are one bucket.
UNPLANNED = ("done", "running", "review", "not_started", "abandoned")
#: A task the plan placed in no bucket (a scheduler gap): its own bucket, shown only when
#: non-zero, so the buckets still sum to the total and the gap is visible rather than hidden.
OTHER = "unplaced"

LABELS = {
    "done": "done",
    "running": "running",
    "ready": "ready",
    "held_by_cap": "held by cap",
    "blocked": "blocked",
    "review": "review",
    "abandoned": "abandoned",
    "not_started": "not started",
    OTHER: "unplaced",
}


def _ids(rows: Iterable[Any]) -> set[str]:
    return {getattr(r, "id", None) or getattr(r, "item", None) or str(r) for r in rows}


@dataclass(frozen=True)
class Totals:
    """What a queue holds, counted once. Every count is over task / phase / bug RECORDS."""

    #: Task records by bucket, in print order; the values sum to ``total``.
    counts: dict[str, int]
    total: int
    #: Live (not abandoned) fix tasks, and how many of them are done.
    fix: int
    fix_done: int
    #: Whether fix tasks are counted in the headline pair (``[output].status_fix_tasks_in_headline``).
    fix_in_headline: bool
    planned: bool
    #: Phase records: total, and done / abandoned / open (everything else) summing to it.
    phases: dict[str, int] = field(default_factory=dict)
    #: Bug records: total, and open / fixed / invalid summing to it.
    bugs: dict[str, int] = field(default_factory=dict)

    @property
    def abandoned(self) -> int:
        return self.counts["abandoned"]

    @property
    def live(self) -> int:
        """The headline's M: task records not abandoned, fix tasks out unless the knob says in."""
        return self.total - self.abandoned - (0 if self.fix_in_headline else self.fix)

    @property
    def live_done(self) -> int:
        """The headline's N."""
        return self.counts["done"] - (0 if self.fix_in_headline else self.fix_done)

    @property
    def phases_live(self) -> int:
        """Phases not abandoned; the pair the `Progress:` block prints."""
        return self.phases["total"] - self.phases["abandoned"]

    @property
    def bugs_live(self) -> int:
        """Bugs not marked invalid; the pair the `Progress:` block prints."""
        return self.bugs["total"] - self.bugs["invalid"]

    def definition(self) -> str:
        """The headline sentence: what N and M count, and what is listed apart."""
        tail = (
            f"{self.abandoned} abandoned listed separately; {self.fix} fix tasks included"
            if self.fix_in_headline
            else f"{self.abandoned} abandoned and {self.fix} fix tasks listed separately"
        )
        return f"{self.live_done} of {self.live} live tasks done ({tail})"

    def bucket_line(self) -> str:
        """Every bucket of every task record, summing to the total."""
        parts = " + ".join(f"{n} {LABELS[k]}" for k, n in self.counts.items())
        note = (
            ""
            if self.planned
            else " (not started = ready, held by a cap and blocked; `ddflow status` splits them)"
        )
        return f"All {self.total} task records: {parts} = {self.total}{note}"

    def phase_line(self) -> str:
        p = self.phases
        return f"Phases: {p['done']} done + {p['open']} open + {p['abandoned']} abandoned = {p['total']}"

    def bug_line(self) -> str:
        b = self.bugs
        return (
            f"Bugs: {b['open']} open + {b['fixed']} fixed + {b['invalid']} invalid = {b['total']}"
        )

    def lines(self) -> list[str]:
        """The block a report prints under its headline."""
        return [self.bucket_line(), self.phase_line(), self.bug_line()]

    def as_json(self) -> dict[str, Any]:
        """The additive fields of the status body: the pre-existing keys keep their meaning."""
        return {
            "tasks": {
                **self.counts,
                "total": self.total,
                "live": self.live,
                "live_done": self.live_done,
                "fix": self.fix,
                "fix_done": self.fix_done,
                "fix_in_headline": self.fix_in_headline,
                "definition": self.definition(),
            },
            "phases": dict(self.phases),
            "bugs": dict(self.bugs),
        }


def queue_totals(
    st: State,
    *,
    fix_in_headline: bool = False,
    plan: Any = None,
) -> Totals:
    """The totals of ``st``'s queue. ``plan`` (a ``schedule.Plan``) splits the open tasks into
    running / ready / held by cap / blocked / review; without one they are ``not_started``
    (plus the RUNNING and REVIEW the log itself records)."""
    tasks: list[Item] = st.tasks()
    if plan is not None:
        placed = [
            ("running", _ids(plan.running)),
            ("review", _ids(plan.review)),
            ("ready", _ids(plan.ready)),
            ("held_by_cap", set(plan.capped)),
            ("blocked", _ids(plan.blocked)),
        ]
        order = PLANNED
    else:
        placed = []
        order = UNPLANNED
    counts = dict.fromkeys(order, 0)
    fix = fix_done = 0
    for t in tasks:
        if t.fixes and t.state != ABANDONED:
            fix += 1
            fix_done += t.state == DONE
        if t.state == DONE:
            counts["done"] += 1
        elif t.state == ABANDONED:
            counts["abandoned"] += 1
        elif plan is None:
            counts[
                "running"
                if t.state == RUNNING
                else "review"
                if t.state == REVIEW
                else "not_started"
            ] += 1
        else:
            bucket = next((name for name, ids in placed if t.id in ids), OTHER)
            counts[bucket] = counts.get(bucket, 0) + 1
    if not counts.get(OTHER):
        counts.pop(OTHER, None)
    phases = st.phases()
    ph_done = sum(p.state == DONE for p in phases)
    ph_aband = sum(p.state == ABANDONED for p in phases)
    bugs = list(st.bugs.values())
    invalid = sum(b.resolution == "invalid" for b in bugs)
    fixed = sum(b.resolution == "fixed" for b in bugs)
    return Totals(
        counts=counts,
        total=len(tasks),
        fix=fix,
        fix_done=fix_done,
        fix_in_headline=fix_in_headline,
        planned=plan is not None,
        phases={
            "total": len(phases),
            "done": ph_done,
            "open": len(phases) - ph_done - ph_aband,
            "abandoned": ph_aband,
        },
        bugs={
            "total": len(bugs),
            "open": len(bugs) - invalid - fixed,
            "fixed": fixed,
            "invalid": invalid,
        },
    )

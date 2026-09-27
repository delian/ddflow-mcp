"""`event.kind` -> a verb a person reads.

Its own module so the command renderer and the CLI can share ONE table. It was a
private name in `cli.py`, which meant a command module wanting to render an event
had to import from the surface it was extracted out of.
"""

from __future__ import annotations

HISTORY_VERBS: dict[str, str] = {
    "phase.added": "phase added",
    "task.added": "task added",
    "task.updated": "updated",
    "task.removed": "removed from the queue",
    "phase.removed": "removed from the queue",
    "item.blocked": "blocked",
    "item.unblocked": "unblocked",
    "item.abandoned": "abandoned",
    "item.completed": "completed",
    "lease.acquired": "claimed",
    "lease.renewed": "heartbeat",
    "lease.released": "released",
    "lease.expired": "lease EXPIRED",
    "gate.recorded": "gate",
    "worktree.created": "worktree created",
    "worktree.removed": "worktree removed",
    "merge.performed": "merged",
    "session.started": "session opened",
    "session.prompt": "operator said",
    "session.note": "noted",
    "session.ended": "session closed",
    "lesson.recorded": "lesson",
    "decision.recorded": "DECISION",
    "decision.superseded": "decision superseded",
    "research.recorded": "research",
    "memory.recorded": "remembered",
    "job.started": "job started",
    "job.ended": "job ended",
    "memory.forgotten": "forgot",
    "bug.found": "BUG found",
    "bug.fixed": "bug fixed",
    "cadence.ran": "cadence ran",
}

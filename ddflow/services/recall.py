"""Periodic mid-work re-recall of the pre-recorded lessons (Bcd5871e71b).

Pre-recorded lessons reach an agent at task start (brief injection) and on a manual
`ddflow recall`. During a long task, wave or session goal nothing re-surfaces them, so an
agent keeps repeating a lesson it has already violated. This module is the bounded re-recall:
it re-scores the LIVE lessons against an item a worker still holds, on a cadence, and says
which lessons still (or newly) match -- reusing `services.similar`, the TF-IDF cosine engine
the add-time duplicate check and `ddflow similar` share, never a second engine.

Nothing here runs on its own: the caller (`ddflow.api.lifecycle.heartbeat`, which an agent
already runs periodically during long gates) asks "is a re-scoring due now?" and, when it is,
records a `cadence.ran` under `recall_recheck:<item>` so the next one waits another
`[cadence].recall_recheck_s`. That record being in the log is what bounds it -- there is no
state file to drift and no timer to busy-loop. Pure except for the run the caller writes.

In `services/` rather than a surface because the heartbeat is an `api/` caller and may not
import a surface; the same reason `services/cadence.py` lives here.
"""

from __future__ import annotations

from typing import Any

from ..config import Config
from ..core.model import State
from ..infra.store import similar_records
from . import similar as sim
from .schedule import last_run_epoch

#: The `cadence.ran` subject a re-scoring run is recorded under: `recall_recheck:<item>`.
CADENCE = "recall_recheck"


def cadence_id(item: str) -> str:
    """The `cadence.ran` subject that records re-scorings of ``item`` and bounds the next."""
    return f"{CADENCE}:{item}"


def matched_lessons(st: State, item: str, cfg: Config) -> list[tuple[str, float]]:
    """The live, uncontested lessons scoring at least `[dedupe].show_floor` against the
    item's text, best first as ``(id, score)``. An absent item simply matches nothing. The
    item itself can never be among them: it is a task/phase (``st.items``), while these are
    the ``st.lessons`` records, so a task that is its own topic scores no self."""
    it = st.items.get(item)
    if it is None:
        return []
    records = [r for r in similar_records(st) if r["kind"] == "lesson"]
    if not records:
        return []
    index = sim.build(records)
    hits = index.query(
        {"title": it.title, "body": f"{it.body} {' '.join(it.tags)}"},
        limit=None,
        kinds=("lesson",),
    )
    return [(i, s) for i, s in hits if s >= cfg.dedupe.show_floor]


def due(st: State, item: str, cfg: Config, now: float) -> bool:
    """Is a re-scoring due now for ``item``? Never for a turned-off cadence
    (`[cadence].recall_recheck_s` 0), an item nobody holds, one that is done/removed, or
    one that has already used its `recall_recheck_max` budget. Otherwise once
    `recall_recheck_s` has elapsed since the last recorded re-scoring -- or since the
    claim, if it has never been re-scored."""
    rc = cfg.cadence
    if rc.recall_recheck_s <= 0:
        return False
    it = st.items.get(item)
    lease = it.lease if it is not None else None
    if it is None or it.removed or it.terminal or lease is None:
        return False
    runs = st.cadences.get(cadence_id(item), [])
    if rc.recall_recheck_max and len(runs) >= rc.recall_recheck_max:
        return False
    last = last_run_epoch(st, cadence_id(item)) or lease.acquired_at
    return (now - last) >= rc.recall_recheck_s


def run_note(hits: list[tuple[str, float]]) -> dict[str, Any]:
    """The `cadence.ran` evidence for one re-scoring: what it surfaced and how much."""
    return {"surfaced": [rid for rid, _ in hits], "scores": dict(hits)}

"""The one settle-a-phase guard both phase doors share (r-reuse-core).

`phase close` and `phase complete` verify the same thing before either writes its event:
the phase exists, is a phase, is not already done, and every task under it is done or
abandoned. They differ only in which half of "imported" work they serve (that check stays
each caller's), the evidence they write, and the event. This module is the shared part,
so the two doors cannot drift apart.

Both functions return ``(None, ...)`` on success or ``(outcome, ...)`` to return verbatim
-- the refusal/failure contract, factored instead of copied.
"""

from __future__ import annotations

from ..core import outcome as O
from ..core.model import DONE, State

_SHOWN = 8


def resolve_phase(st: State, kind: str, phase_id: str):
    """``(None, it)`` for the phase ``phase_id`` (missing, removed or a task are refused),
    else ``(outcome, None)``, which the caller returns verbatim.

    The caller still decides what "imported" means for its door and checks the returned
    phase for that itself.
    """
    it = st.items.get(phase_id)
    if it is None or it.removed:
        gone = " (it was removed from the queue)" if it is not None else ""
        return O.failed(kind, f"no such item {phase_id!r}{gone}", id=phase_id), None
    if it.kind != "phase":
        return (
            O.refused(
                kind,
                f"{phase_id} is a {it.kind}, not a phase: "
                f"`ddflow complete {phase_id}` finishes an item",
                id=phase_id,
            ),
            None,
        )
    return None, it


def settled_tasks(it, st: State, kind: str, phase_id: str, *, hint: str):
    """``(None, tasks, done)`` when every task under ``it`` is done or abandoned, else
    ``(outcome, None, None)``. ``hint`` names what to do with still-open work, per door.
    """
    if it.terminal:
        return O.refused(kind, f"{phase_id} is already {it.state}", id=phase_id), None, None
    tasks = st.tasks(phase_id)
    # A gate-skip follow-up (`followup_of` set) is never the phase's remaining work: it
    # is settled with its item (on complete, abandon, or the gate re-recorded), so it must
    # not hold the phase open (bug B-gskip-task-lands-in-phase-and-blocks). It stays in
    # `tasks` below so the "work to retire" count still sees the phase, but it never
    # blocks the close.
    open_tasks = [t for t in tasks if not t.terminal and not t.followup_of]
    if open_tasks:
        shown = ", ".join(t.id for t in open_tasks[:_SHOWN])
        more = " ..." if len(open_tasks) > _SHOWN else ""
        return (
            O.refused(
                kind,
                f"{len(open_tasks)} task(s) under {phase_id} are still open: "
                f"{shown}{more} -- {hint}",
                id=phase_id,
                open=len(open_tasks),
            ),
            None,
            None,
        )
    if not tasks:
        return (
            O.refused(
                kind,
                f"nothing is under {phase_id}: there is no finished work to retire. "
                f"A phase with tasks to come is not finished",
                id=phase_id,
            ),
            None,
            None,
        )
    done = sum(1 for t in tasks if t.state == DONE)
    return None, tasks, done

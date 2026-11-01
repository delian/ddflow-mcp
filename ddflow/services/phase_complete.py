"""Deliberately retire a phase whose every task is settled, operator-gated.

A phase flips to done on its own only when every task under it completes, and there is no
deliberate retire: an operator who wants to mark a phase complete once its remaining work is
settled cannot, outside `complete --force`, which records nothing about WHY no phase pipeline
ran. `ddflow phase close` exists ONLY for work that predates ddflow (the importer's phases).

This module is that door and nothing else. It verifies every task under the phase is done or
abandoned (refusing with the open ids otherwise), leaves a phase the importer wrote to
`phase close`, and records `phase.completed` -- a first-class event a reader can tell apart
from `item.completed`'s pipeline completion and from `phase close`'s imported close. An agent
may not do it on its own: it files an operator approval like every other operator act.
"""

from __future__ import annotations

from typing import Any

from ..core import outcome as O
from ..core.digest import content_digest
from ..core.model import DONE, State, fold
from . import approval as AP
from .approvals import consume as CN
from .approvals.canon import Canon

# The importer's own predicate, not a second copy of it: "a phase the IMPORTER wrote" is the
# one set `phase close` may close, and so the one set this command must leave to it.
from .importer import _is_imported_phase

KIND = "phase.completed"
_SHOWN = 8


def complete_phase(
    log,
    cfg,
    st: State,
    phase_id: str,
    *,
    approval: str = "",
    agent: str = "",
) -> O.Outcome:
    """Complete a phase whose every task is done or abandoned, for a person directly and
    for an agent only by an operator approval of exactly this settled state.

    Distinct from `phase close` (imported work) and from `complete` (an item's pipeline):
    this is the deliberate retire, and it writes `phase.completed`, so a reader knows the
    phase settled without any phase pipeline running.
    """
    it = st.items.get(phase_id)
    if it is None or it.removed:
        gone = " (it was removed from the queue)" if it is not None else ""
        return O.failed(KIND, f"no such item {phase_id!r}{gone}", id=phase_id)
    if it.kind != "phase":
        return O.refused(
            KIND,
            f"{phase_id} is a {it.kind}, not a phase: `ddflow complete {phase_id}` finishes an item",
            id=phase_id,
        )
    if _is_imported_phase(it):
        return O.refused(
            KIND,
            f"{phase_id} was created by `ddflow import`: `ddflow phase close {phase_id} "
            f"--imported` is the door for work that predates ddflow",
            id=phase_id,
        )
    if it.terminal:
        return O.refused(KIND, f"{phase_id} is already {it.state}", id=phase_id)
    tasks = st.tasks(phase_id)
    open_tasks = [t for t in tasks if not t.terminal]
    if open_tasks:
        shown = ", ".join(t.id for t in open_tasks[:_SHOWN])
        more = " ..." if len(open_tasks) > _SHOWN else ""
        return O.refused(
            KIND,
            f"{len(open_tasks)} task(s) under {phase_id} are still open: {shown}{more} -- "
            f"complete or abandon them first",
            id=phase_id,
            open=len(open_tasks),
        )
    if not tasks:
        return O.refused(
            KIND,
            f"nothing is under {phase_id}: there is no finished work to retire. A phase with "
            f"tasks to come is not finished",
            id=phase_id,
        )
    done = sum(1 for t in tasks if t.state == DONE)
    stamp: dict[str, Any] = {}
    if AP.agent_marker(agent):
        canon = Canon(
            verb="phase.complete",
            agent=log.agent_id,
            item=phase_id,
            state=content_digest("\n".join(sorted(f"{t.id}:{t.state}" for t in tasks))),
        )
        decided = CN.operator_gate(log, cfg, canon, approval_id=approval, auto=False)
        if not decided.ok:
            return O.refused(
                KIND, "refusing: " + decided.message, id=phase_id, approval=decided.ask_id
            )
        stamp = {"approval": decided.stamp}
    by = agent or "operator"
    evidence = (
        f"retired by {by}: every one of the {len(tasks)} task(s) under it is done or "
        f"abandoned ({done} done, {len(tasks) - done} abandoned)"
    )
    log.append(
        "phase.completed",
        phase_id,
        {"kind": "phase", "evidence": evidence, "tasks": len(tasks), **stamp},
    )
    return O.ok(
        KIND,
        id=phase_id,
        evidence=evidence,
        tasks=len(tasks),
        done=done,
        abandoned=len(tasks) - done,
    )


def complete(log, cfg, phase_id: str, *, approval: str = "", agent: str = "") -> O.Outcome:
    """The transaction around `complete_phase`: fold fresh, then check, gate and write.

    Folding INSIDE the transaction, like `phase_close`: a task another agent closed since the
    caller loaded is then seen, so the check, the approval digest and the write cannot
    disagree.
    """
    with log.transaction():
        now = fold(log.read_all(), strict=False)
        return complete_phase(log, cfg, now, phase_id, approval=approval, agent=agent)

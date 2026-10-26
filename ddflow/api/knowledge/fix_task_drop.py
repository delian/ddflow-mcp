"""Dropping the fix task of a bug that is closed: what is removed, what is kept, and why."""

from __future__ import annotations

from ...core.model import ABANDONED, OPEN, fold
from ...services import leases as L


def _ended_by_abandon(log, item: str) -> bool:
    """Whether the item's last ending event was an abandonment (by this very call)."""
    it = fold(log.read_all(), strict=False).items.get(item)
    return it is not None and it.state == ABANDONED


#: Why `bug invalid` left a fix task alone (`fix_task_kept`), with the words the surfaces
#: say: the queue still wants it (`STILL_QUEUED`), or there is nothing to remove. "" when
#: the bug has no fix task or it was removed. ONE vocabulary, read by the CLI, so a
#: reason added here cannot fall through to the wrong sentence there (roborev, job 1300).
STILL_QUEUED: dict[str, str] = {
    "held": "somebody holds it",
    "needed": "an item is filed under it or needs it",
    "shared": "it fixes another open bug too",
}
NOTHING_TO_REMOVE: dict[str, str] = {
    "finished": "already finished",
    "removed": "already removed from the queue",
    "missing": "not in the queue at all",
}


def _drop_fix_task(
    log, st, rec, *, reason: str = "", closed: str = "as invalid"
) -> tuple[str, str]:
    removed, why = _drop_fix_task_unchecked(log, st, rec, reason=reason, closed=closed)
    assert not why or why in STILL_QUEUED or why in NOTHING_TO_REMOVE, why
    return removed, why


def _drop_fix_task_unchecked(
    log, st, rec, *, reason: str = "", closed: str = "as invalid"
) -> tuple[str, str]:
    """Take a false finding's fix task out of the queue, when nothing else wants it: it is
    still OPEN -- or, under the CALLER's own lease, in any non-terminal state (RUNNING,
    BLOCKED), when it is abandoned and released rather than removed (B8f9668d27d); a task
    held by anyone else stays. No other open bug may name it, nothing is filed under it and
    nothing `needs` it -- the guards `api.remove` applies, so a removal here strands no
    one. Returns ``(removed id, "")`` or ``("", why kept)``, the reason a key of
    `STILL_QUEUED` or `NOTHING_TO_REMOVE` (asserted, so a new reason cannot reach the
    surfaces without its sentence),
    so the reply can say what is true: a task somebody has claimed, or that fixes a real
    bug too, stays and the agent decides; a finished or removed one is not "still queued"."""

    if not rec.fix_task:
        return "", ""
    t = st.items.get(rec.fix_task)
    if t is None:
        return "", "missing"
    if t.removed:
        return "", "removed"
    mine = bool(t.lease) and t.lease.holder == log.agent_id
    if t.lease and not mine:  # before the state: a claimed task is RUNNING, and held is the point
        return "", "held"
    if t.terminal or (t.state != OPEN and not mine):
        return "", "finished"
    if any(b.open and b.id != rec.id and b.fix_task == t.id for b in st.bugs.values()):
        return "", "shared"
    if st.open_descendants(t.id) or any(
        not o.removed and t.id in o.needs for o in st.items.values()
    ):
        return "", "needed"
    why = f"bug {rec.id} closed {closed}" + (f": {reason}" if reason else "")
    if mine:  # the caller's own lease: end the task and release it, as `abandon` does
        log.append("item.abandoned", t.id, {"reason": why, "kind": t.kind})
        L.release(log, t.id, note=f"abandoned: {why}")
        return t.id, ""
    log.append("task.removed", t.id, {"reason": why})
    return t.id, ""

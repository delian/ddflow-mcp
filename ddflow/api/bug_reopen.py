"""`bug reopen`: undo a bug closure made by mistake; and the re-filing of the bugs a
completion leaves open (B7bdcc6b212).

Completing fix-B297ede2447 closed four bugs it never fixed, and there was no way back: a
re-report of a closed id merges into the record without reopening it, and `bug invalid`
would have written a false finding. A reopen is its own event, with the reason, so the
history keeps both the closure and its undoing.
"""

from __future__ import annotations

from pathlib import Path

from ..core import outcome as O
from ..core.model import ABANDONED, DONE, fold
from ..services.completion import fixes_of
from ._base import _load


def _fix_task_after(st, bug) -> str:
    """The task that fixes the reopened bug: its current `fix_task` if that is still open
    and was filed to fix it, else its own open `fix-<bug>`, else "" -- so `bug file-tasks`
    files one rather than leaving it pointed at a finished task that never fixed it."""
    for tid in (bug.fix_task, f"fix-{bug.id}"):
        it = st.items.get(tid) if tid else None
        if it is None or it.removed or it.state in (DONE, ABANDONED):
            continue
        if bug.id in fixes_of(st, tid):
            return tid
    return ""


def bug_reopen(repo: Path, bug: str, *, reason: str, agent: str = "") -> O.Outcome:
    """Reopen a closed (fixed or invalid) bug, saying why. Refused for an unknown or an
    open bug; ``reason`` is required."""
    reason = " ".join((reason or "").split())
    if not reason:
        return O.failed("bug.reopened", "--reason is required: say why the closure was wrong")
    log, _cfg, _st = _load(repo, agent)
    with log.transaction():
        st = fold(log.read_all(), strict=False)
        rec = st.bugs.get(bug)
        if rec is None:
            return O.refused("bug.reopened", f"no bug {bug} is recorded in this log.", id=bug)
        if rec.open:
            return O.refused(
                "bug.reopened", f"bug {bug} is open; there is nothing to reopen.", id=bug
            )
        was = rec.resolution
        fix_task = _fix_task_after(st, rec)
        log.append("bug.reopened", bug, {"reason": reason, "was": was, "fix_task": fix_task})
    return O.ok(
        "bug.reopened",
        id=bug,
        was=was,
        reason_given=reason,
        fix_task=fix_task,
        previous_fix_task=rec.fix_task,
    )


def refile_reported(log, cfg, item: str, bugs: list[str]) -> dict[str, str]:
    """Give each bug reported against ``item`` -- left open by its completion, which closes
    only what the task was filed to fix (`CM.reported_against`) -- a fix task of its own,
    as `bug found` would have filed it, so it is not left pointing at a finished task
    that nothing will refile (`bug file-tasks` counts a done task as its fix). Called
    after ``item`` is DONE, so the open-fix-task link cannot be taken again. Returns
    {bug: fix task}; nothing under `[bugs] file_task = false`."""
    if not bugs or not cfg.bugs.file_task:
        return {}
    from .knowledge import _file_fix_task

    out: dict[str, str] = {}
    with log.transaction():
        st = fold(log.read_all(), strict=False)
        for bid in bugs:
            b = st.bugs.get(bid)
            if b is None or not b.open or b.fix_task != item:
                continue
            fix = _file_fix_task(
                log, cfg, st, bid, title=b.title, summary=b.summary, item=item, globs=""
            )
            log.append("bug.found", bid, {"fix_task": fix["fix_task"]})
            out[bid] = fix["fix_task"]
    return out

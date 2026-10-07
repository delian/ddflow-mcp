"""`bug reopen`: undo a bug closure made by mistake; and the re-filing of the bugs a
completion leaves open (B7bdcc6b212).

Completing fix-B297ede2447 closed four bugs it never fixed, and there was no way back: a
re-report of a closed id merges into the record without reopening it, and `bug invalid`
would have written a false finding. A reopen is its own event, with the reason, so the
history keeps both the closure and its undoing.
"""

from __future__ import annotations

from pathlib import Path

from ..core import ids as IDS
from ..core import outcome as O
from ..core.model import ABANDONED, DONE, fold
from ..services.completion import fixes_of
from ._base import _load


def _fix_task_after(st, cfg, bug) -> str:
    """The task that fixes the reopened bug: a task filed to fix it (its current
    `fix_task`, else its own `fix-<bug>`), an open one first. A DONE one is kept -- it is
    the bug's own fix, which did not hold, and `ddflow verify <task> --reopen` sends it
    back to the queue. An ABANDONED one is named too, so the reader sees what became of
    it; `bug file-tasks` files the next (`_next_step`). "" when no task was filed to
    fix it (say, the finished task it was merely reported against), so `bug file-tasks`
    files one."""
    mine = [
        st.items[t]
        for t in dict.fromkeys((bug.fix_task, IDS.render(cfg, "fix_task", parent=bug.id)))
        if t in st.items and not st.items[t].removed and bug.id in fixes_of(st, t, cfg)
    ]
    for it in mine:
        if not it.terminal:
            return it.id
    # Then a finished one: DONE before ABANDONED, so a fix that landed is the one named.
    return next((it.id for st_ in (DONE, ABANDONED) for it in mine if it.state == st_), "")


def _next_step(bug: str, task: str, state: str) -> str:
    """What to do about the reopened bug's fix, for every surface alike."""
    if not task:
        return "no task was filed to fix it: `ddflow bug file-tasks` files one"
    if state == ABANDONED:
        # Nothing revives an abandoned item: `bug file-tasks` files the bug a new one
        # under a fresh id (`fix-<bug>-2`, B974e34fa83).
        return (
            f"its fix task {task} was abandoned (nothing revives it): `ddflow bug "
            f"file-tasks` files it a new one, or `ddflow bug fixed {bug} --regression-test "
            f"<test>` closes it once a regression test that fails on the unfixed code is in"
        )
    if state == DONE:
        return f"its fix task {task} is done: `ddflow verify {task} --reopen` reopens it"
    return f"fix task: {task}"


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
        fix_task = _fix_task_after(st, _cfg, rec)
        log.append("bug.reopened", bug, {"reason": reason, "was": was, "fix_task": fix_task})
    state = st.items[fix_task].state if fix_task else ""
    return O.ok(
        "bug.reopened",
        id=bug,
        was=was,
        reason_given=reason,
        fix_task=fix_task,
        fix_task_state=state,
        previous_fix_task=rec.fix_task,
        next=_next_step(bug, fix_task, state),
    )


def refile_reported(log, cfg, item: str, bugs: list[str]) -> dict[str, str]:
    """Give each bug reported against ``item`` -- left open by its completion, which closes
    only what the task was filed to fix (`CM.reported_against`) -- a fix task of its own,
    as `bug found` would have filed it, so it is not left pointing at a finished task
    that nothing will refile (`bug file-tasks` counts a done task as its fix). Called
    just before ``item``'s completion is written, and decided as if it were DONE, so the
    open-fix-task link back to ``item`` cannot be taken again; ``item`` still lends its
    phase, globs, priority and line. A `fix-<bug>` already in the queue is linked, not
    filed twice. Returns {bug: fix task}; nothing under `[bugs] file_task = false`."""
    if not bugs or not cfg.bugs.file_task:
        return {}
    from .knowledge import _file_fix_task

    out: dict[str, str] = {}
    with log.transaction():
        st = fold(log.read_all(), strict=False)
        # `fold` builds a fresh State from the events (no cache), so this mark lives only
        # in this decision: the completion is going through, and is written next.
        if item in st.items:
            st.items[item].state = DONE
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

"""Closing a bug as invalid: a false finding, with its fix task dropped."""

from __future__ import annotations

from pathlib import Path

from ...core import outcome as O
from ...core.model import fold
from .._base import _load
from .bugs import _unknown_bug
from .fix_task_drop import _drop_fix_task, _ended_by_abandon
from .regression import _unresolved_tests


def bug_invalid(
    repo: Path, bug: str, *, reason: str, evidence: str = "", agent: str = ""
) -> O.Outcome:
    """Close a bug as a FALSE finding: nothing was broken, so nothing was fixed.

    `bug fixed` was the only closure, and it claims a repair plus a regression test that
    fails on the unfixed code. A finding shown false has neither, so B97355c6d15 stayed
    open forever -- and closing it as fixed would have recorded a repair nobody made. This
    closure never sets `fixed_at` and never counts as a fix.

    Refused (exit 3): an unknown id, a bug already closed either way (a fixed bug is not
    re-labelled false after the fact), and an empty reason -- "invalid" with no why is an
    unexplained dismissal. `evidence` is the probe that showed it false: a command, or a
    test node id, which is resolved statically as `--regression-test` is (exit 1 when it
    names nothing).
    """
    log, _cfg, st = _load(repo, agent)
    if not reason.strip():
        return O.refused(
            "bug.invalid",
            "a bug may not be closed as invalid without --reason saying why the finding "
            "is false; pass --evidence with the probe or test that showed it.",
            id=bug,
        )
    if bug not in st.bugs:
        return _unknown_bug("bug.invalid", bug, st)
    rec = st.bugs[bug]
    if rec.resolution == "fixed":
        return O.refused(
            "bug.invalid",
            f"bug {bug} is already closed as fixed (regression test: "
            f"{rec.regression_test or 'none recorded'}); a fixed bug is not re-labelled "
            f"a false finding.",
            id=bug,
        )
    if rec.resolution == "invalid":
        return O.refused(
            "bug.invalid",
            f"bug {bug} is already closed as invalid: {rec.invalid_reason}",
            id=bug,
        )
    missing, unchecked = _unresolved_tests(repo, evidence)
    if missing:
        return O.failed(
            "bug.invalid",
            f"--evidence names a test that exists in no worktree of this repository: "
            f"{', '.join(missing)}. Name the test or probe that shows the finding false.",
            id=bug,
        )
    reason = reason.strip()
    with log.transaction():
        log.append("bug.invalid", bug, {"reason": reason, "evidence": evidence})
        # Decided from the log as it is NOW, under the lock: a claim on the fix task made
        # since `_load` must be seen, or the task is removed under its holder.
        fresh = fold(log.read_all(), strict=False)
        rec = fresh.bugs.get(bug, rec)  # the record the removal is decided from, and reported
        removed, kept = _drop_fix_task(log, fresh, rec, reason=reason)
        task = fresh.items.get(rec.fix_task) if rec.fix_task else None
        holder = task.lease.holder if kept == "held" and task and task.lease else ""
    # A task ended from the caller's own lease was ABANDONED, not removed (B8f9668d27d). Its
    # worktree is NOT disposed here: this call cannot see where the caller's shell stands
    # (over MCP the process cwd is the server's), so the tree stays for `ddflow cleanup`.
    abandoned = removed if removed and _ended_by_abandon(log, removed) else ""
    tree = ""
    if abandoned:
        wt = fold(log.read_all(), strict=False).items[abandoned].worktree
        tree = (
            f"worktree {wt} kept; `ddflow cleanup` reports it and removes it once clean"
            if wt
            else ""
        )
    return O.ok(
        "bug.invalid",
        id=bug,
        invalid_reason=reason,
        evidence=evidence,
        unchecked=unchecked,
        fix_task=rec.fix_task,
        fix_task_removed="" if abandoned else removed,
        fix_task_abandoned=abandoned,
        fix_task_kept=kept,
        fix_task_holder=holder,
        fix_task_tree=tree,
    )

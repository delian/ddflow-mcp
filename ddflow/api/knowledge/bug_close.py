"""Closing a bug: fixed (with its regression test) or invalid (with its fix task dropped)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import ddflow.api._dedupe as DD

from ...core import outcome as O
from ...core.events import parse_changelog
from ...core.model import fold
from .._base import _load
from .bugs import _unknown_bug
from .regression import _looks_like_several, _split_outside_brackets, _unresolved_tests


def bug_fixed(
    repo: Path,
    item: str,
    *,
    regression_test: str | list[str] = "",
    lesson: str = "",
    lesson_title: str = "",
    lesson_rule: str = "",
    agent: str = "",
    changelog: str = "",
    verify_regression: bool = True,
    verify_reason: str = "",
) -> O.Outcome:
    """Close a bug. Refuses without the test that would catch it again.

    `regression_test` is one test or several: a list (a repeated CLI flag, an MCP
    array), each entry itself split on ',' and ';' outside a parametrize id's brackets
    (B227585c781). The event keeps `regression_test` as the string every reader already
    displays -- as given (stripped) when one string was given, ', '-joined from a list --
    and `regression_tests` as the split list. The required-test rule asks the LIST: `;`
    alone is a truthy string naming no test.

    The named test is then VERIFIED (B-bugfix-verified): it is run on the pre-fix source
    (the fix task's base -- for a landed fix, the target just before its landing -- with
    the new test file) and on the fixed tree, and the bug is
    REFUSED when it passes on the pre-fix tree (it does not catch the bug) or fails on the
    fixed one. When the comparison cannot be made -- no pytest runner, no fix task with a
    worktree, no base ref -- the check is `could-not-run`, recorded, and the bug closes
    with the gap on the record rather than locked open. `verify_regression=False` with a
    `verify_reason` records the override instead; a reason is required and is recorded.
    """
    entry: dict[str, Any] = {}
    if changelog:
        try:
            entry = parse_changelog(changelog)
        except ValueError as e:
            return O.failed("bug.fixed", str(e), id=item)
    log, cfg, st = _load(repo, agent)
    parts = [regression_test] if isinstance(regression_test, str) else list(regression_test)
    tests = [t for part in parts for t in _split_outside_brackets(str(part or ""))]
    regression_test = (
        regression_test.strip() if isinstance(regression_test, str) else ", ".join(tests)
    )
    if not tests and cfg.lessons.require_regression_test:
        return O.failed(
            "bug.fixed",
            "a bug may not be closed without --regression-test naming the test that "
            "would catch it again. Write the test, watch it FAIL against the unfixed "
            "code, then close.",
            id=item,
        )
    if item not in st.bugs:
        return _unknown_bug("bug.fixed", item, st)
    missing, unchecked = _unresolved_tests(repo, regression_test)
    if missing:
        joined = [m for m in missing if _looks_like_several(m)]
        hint = (
            f" {'; '.join(repr(m) for m in joined)} looks like several tests in one entry: "
            f"separate them with ',' or ';', or repeat --regression-test."
            if joined
            else ""
        )
        return O.failed(
            "bug.fixed",
            f"--regression-test: {len(missing)} of {len(tests)} test(s) exist in no "
            f"worktree of this repository: {'; '.join(missing)}.{hint} Name the tests "
            f"that now guard this bug.",
            id=item,
        )
    if not verify_regression and not verify_reason.strip():
        return O.refused(
            "bug.fixed",
            "--skip-regression-verify needs --verify-reason saying why; the override is "
            "recorded so a later reader knows the pre-fix check was skipped, and why.",
            id=item,
        )
    verified, verify_ev = _verify_regression(
        repo, cfg, st, item, tests, verify_regression=verify_regression, reason=verify_reason
    )
    if verified in ("passed-on-prefix", "failed-on-fix"):
        return O.refused(
            "bug.fixed",
            f"cannot close {item}: {verify_ev.get('reason', '')}. Name a test that FAILS "
            f"without the fix, or close with --skip-regression-verify --verify-reason "
            f"'<why>' (recorded).",
            id=item,
        )
    log.append(
        "bug.fixed",
        item,
        {
            "regression_test": regression_test,
            "regression_tests": tests,
            "lesson": lesson,
            "regression_verified": verified,
            **({"regression_verify": verify_ev} if verify_ev else {}),
            **({"changelog": entry} if entry else {}),
        },
    )
    capture: dict[str, Any] = {}
    if cfg.lessons.auto_capture_on_bug and lesson_title:
        capture = _capture_lesson(repo, log, cfg, st, item, lesson_title, lesson_rule)
    return O.ok(
        "bug.fixed",
        id=item,
        regression_test=regression_test,
        regression_tests=tests,
        regression_verified=verified,
        regression_verify=verify_ev,
        unchecked=unchecked,
        # The lesson that now holds the text: the new one, or the one it was added to.
        lesson_captured=capture.get("captured") or capture.get("extended", ""),
        lesson_capture=capture,
    )


def _capture_lesson(repo: Path, log, cfg, st, bug: str, title: str, rule: str) -> dict[str, Any]:
    """The lesson `bug fixed --lesson-title` captures, through the same duplicate check as
    `lesson add` (B4ea345b51e: it was appended unchecked, so the same lesson was filed
    twice). Nobody can answer a question here -- the bug is already closed -- so the
    answer is automatic and on the record (`dedupe.auto`): an identical open lesson
    receives the text (no new id), and a lesson that merely reads like one is filed
    LINKED to it (`related`), so the two meet in the next sweep instead of drifting.
    Returns `lesson_capture`: {captured, extended | related | not_captured, candidates,
    dedupe_unavailable}, as `chk.data()` names them."""
    rid = f"L-{bug}"
    rec = DD.Record(
        kind="lesson", event_kind="lesson.recorded", rid=rid, title=title, body=rule, item=bug
    )
    chk = DD.check_add(repo, log, cfg, st, rec)
    if chk.refusal is not None and chk.shown:
        chk = DD.check_add(repo, log, cfg, st, rec, DD.Answer("related", chk.shown[0]["id"]))
        if "dedupe" in chk.fields:
            chk.fields["dedupe"]["auto"] = True
    if chk.refusal is not None:
        return {"captured": "", "not_captured": chk.refusal.reason, **chk.data()}
    if chk.extension:
        DD.extend(log, cfg, chk, "lesson.recorded")
        return {"captured": "", "extended": chk.extension["target"], **chk.data()}
    data = {"title": title, "rule": rule, "seen_in": [bug], "tags": ["bug"], **chk.fields}
    with log.transaction():
        log.append("lesson.recorded", rid, data)
        DD.after_add(log, cfg, rid, chk)
    # `chk.data()` carries `related` / `extends` / `duplicate_of`, the candidates shown and
    # `dedupe_unavailable` when the check could not run: never reported as a clean capture.
    return {"captured": rid, **chk.data()}


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
        removed, kept = _drop_fix_task(log, fresh, rec)
    return O.ok(
        "bug.invalid",
        id=bug,
        invalid_reason=reason,
        evidence=evidence,
        unchecked=unchecked,
        fix_task=rec.fix_task,
        fix_task_removed=removed,
        fix_task_kept=kept,
    )


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


def _drop_fix_task(log, st, rec) -> tuple[str, str]:
    removed, why = _drop_fix_task_unchecked(log, st, rec)
    assert not why or why in STILL_QUEUED or why in NOTHING_TO_REMOVE, why
    return removed, why


def _drop_fix_task_unchecked(log, st, rec) -> tuple[str, str]:
    """Take a false finding's fix task out of the queue, when nothing else wants it: it is
    still OPEN, nobody holds it, no other open bug names it, nothing is filed under it and
    nothing `needs` it -- the guards `api.remove` applies, so a removal here strands no
    one. Returns ``(removed id, "")`` or ``("", why kept)``, the reason a key of
    `STILL_QUEUED` or `NOTHING_TO_REMOVE` (asserted, so a new reason cannot reach the
    surfaces without its sentence),
    so the reply can say what is true: a task somebody has claimed, or that fixes a real
    bug too, stays and the agent decides; a finished or removed one is not "still queued"."""
    from ...core.model import OPEN

    if not rec.fix_task:
        return "", ""
    t = st.items.get(rec.fix_task)
    if t is None:
        return "", "missing"
    if t.removed:
        return "", "removed"
    if t.lease:  # before the state: a claimed task is RUNNING, and held is the point
        return "", "held"
    if t.state != OPEN:
        return "", "finished"
    if any(b.open and b.id != rec.id and b.fix_task == t.id for b in st.bugs.values()):
        return "", "shared"
    if st.open_descendants(t.id) or any(
        not o.removed and t.id in o.needs for o in st.items.values()
    ):
        return "", "needed"
    log.append("task.removed", t.id, {"reason": f"bug {rec.id} closed as invalid"})
    return t.id, ""


def _verify_regression(
    repo: Path,
    cfg,
    st,
    bug_id: str,
    tests: list[str],
    *,
    verify_regression: bool,
    reason: str,
) -> tuple[str, dict[str, Any]]:
    """Run a bug's regression test on the pre-fix and fixed trees (B-bugfix-verified).

    Returns (status, evidence). Only pytest NODE IDS are run: a spec or a shell command
    is "not-applicable" (the static resolution already leaves it `unchecked`). A fix
    task that has LANDED is checked against its landing (`landed_before` is the pre-fix
    source, `landed_after` the fixed one), never the base by name, which then holds the
    fix (B0d5253d31f). An unlanded one needs its WORKTREE, the only thing that then names
    a pre-fix source: an unclaimed fix task is "could-not-run", recorded, never
    "verified".
    """
    if not verify_regression:
        return "overridden", {"reason": reason.strip()}
    module_tests = [t for t in tests if "::" in t and t.split("::", 1)[0].endswith(".py")]
    if not module_tests:
        return "not-applicable", {}
    from ...infra import worktree as W
    from ...services import gates as G

    rec = st.bugs.get(bug_id)
    fx = st.items.get(rec.fix_task) if rec is not None and rec.fix_task else None
    if fx is None and rec is not None and rec.item:
        # `bug found --item X --no-task`: X fixes it in the commit that found it, and
        # while X works in a worktree that worktree is the fix (B3eeb47ca9b). Only then:
        # a landed X may have been merely where an older bug was seen, not its fix.
        found_on = st.items.get(rec.item)
        landed = found_on is not None and (found_on.landed_after or found_on.merged_sha)
        if found_on is not None and found_on.worktree and not landed:
            fx = found_on
    if fx is not None and fx.landed_before and fx.landed_after:
        # Merged already: the landing's own before and after, never the base by NAME,
        # which now holds the fix -- the driver merges first and completes after
        # (B0d5253d31f). `tree` is unused: the fixed tree is a checkout of `after`.
        status, ev = G.verify_regression_test(
            repo, cfg, tree=repo, base=fx.landed_before, tests=module_tests, after=fx.landed_after
        )
        return _regression_status(status, ev, G)
    tree = W.load_path(repo, fx.worktree) if fx is not None and fx.worktree else None
    if tree is None:
        return "could-not-run", {
            "reason": "the bug's fix task is not working in a worktree, so there is no "
            "pre-fix source to compare the test against"
        }
    base = fx.base or cfg.worktree.base_ref or ""
    if not base:
        try:
            base = W.default_branch(W.repo_root(tree))
        except W.GitError:
            base = ""
    if not base:
        return "could-not-run", {"reason": "no base ref to build the pre-fix tree from"}
    status, ev = G.verify_regression_test(repo, cfg, tree=tree, base=base, tests=module_tests)
    return _regression_status(status, ev, G)


def _regression_status(status: str, ev: dict[str, Any], G: Any) -> tuple[str, dict[str, Any]]:
    """`verify_regression_test`'s status as the bug record names it (``G``: the gates
    package, passed in so it is imported once, lazily, by the caller)."""
    # Explicitly, ONE success status: an unrecognized status must never fall through to
    # "verified" -- a bug whose test was never shown to fail-first would be recorded as
    # verified, the vacuous pass this whole feature exists to prevent (rubber_duck #1).
    if status == G.REGRESSION_VERIFIED:
        return "verified", ev
    if status == G.REGRESSION_PASSES_ON_PREFIX:
        return "passed-on-prefix", ev
    if status == G.REGRESSION_FAILS_ON_FIX:
        return "failed-on-fix", ev
    if status == G.REGRESSION_COULD_NOT_RUN:
        return "could-not-run", ev
    return "could-not-run", {**ev, "reason": f"unrecognized verification status {status!r}"}

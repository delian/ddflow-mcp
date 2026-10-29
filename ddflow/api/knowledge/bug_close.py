"""Closing a bug: fixed (with its regression test) or invalid (with its fix task dropped)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import ddflow.api._dedupe as DD

from ...core import outcome as O
from ...core.events import parse_changelog
from ...core.model import fold
from ...infra import worktree as W
from ...services import completion as CM
from ...services import gates as G
from .._base import _load
from .bug_lint import _close_lint_exempt, _is_node_id
from .bugs import _unknown_bug
from .fix_task_drop import (  # noqa: F401 -- re-exported by the package
    NOTHING_TO_REMOVE,
    STILL_QUEUED,
    _drop_fix_task,
    _ended_by_abandon,
)
from .regression import _looks_like_several, _split_outside_brackets, _unresolved_tests


def _named_tests(regression_test: str | list[str]) -> tuple[list[str], str]:
    """(the split test list, the string the event keeps) of what `--regression-test` was given."""
    parts = [regression_test] if isinstance(regression_test, str) else list(regression_test)
    tests = [t for part in parts for t in _split_outside_brackets(str(part or ""))]
    shown = regression_test.strip() if isinstance(regression_test, str) else ", ".join(tests)
    return tests, shown


def _half_node_ids(tests: list[str]) -> list[str]:
    """Entries that are a node id with a part missing: ``::test_x`` (the file part empty,
    e.g. from an unset shell variable) or a bare ``tests/test_x.py`` (no test named). A
    shell command or a spec is not one (B-bugfixed-accepts-bare-node-id)."""
    out = []
    for t in tests:
        e = t.strip()
        if e.startswith("::") or (
            e.endswith(".py") and "::" not in e and not any(c.isspace() for c in e)
        ):
            out.append(t)
    return out


def _missing_tests(item: str, missing: list[str], tests: list[str]) -> O.Outcome:
    """The refusal for named tests that exist in no worktree of this repository."""
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


def bug_fixed(  # noqa: PLR0913 -- one keyword per close facet, all keyword-only
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
    mutation: dict[str, str] | None = None,
    landed: str = "",
    lint_exempt: str = "",
) -> O.Outcome:
    """Close a bug. Refuses without the test that would catch it again.

    `lint_exempt` (Bee3becc481): the reason a CI-filed LINT bug (a `ruff format --check`
    failure: its check id is not a pytest node id) has no regression test to name. It closes
    without one, as `regression_verified = "exempt-lint"` with the reason; any other bug is
    refused, and an empty reason is not a reason.

    `landed` (B256be593f0): a commit already on the base that holds the fix, for a fix task
    claimed AFTER its fix reached the base by some other commit -- it has no branch of its
    own and no landing, so the pre-fix source is otherwise unknowable. The test must FAIL on
    the commit's first parent and PASS on the commit; the fix task, now pointless, is ended
    naming the commit (unless somebody else holds it or another open bug needs it).

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

    A fix confined to test files cannot fail on a pre-fix tree that has the new test
    file: `mutation` ({file, old, new}, an edit to a changed test file disabling the
    fix) is then applied by ddflow and the test must fail on it -- recorded as
    `verified-by-mutation` with the mutation and the run (B Bc262739f62).
    """
    entry: dict[str, Any] = {}
    if changelog:
        try:
            entry = parse_changelog(changelog)
        except ValueError as e:
            return O.failed("bug.fixed", str(e), id=item)
    log, cfg, st = _load(repo, agent)
    tests, regression_test = _named_tests(regression_test)
    if lint_exempt:
        return _lint_exempt_close(
            repo, log, cfg, st, item, lint_exempt, tests, entry, mutation, verify_reason, landed
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
        return _missing_tests(item, missing, tests)
    if not verify_regression and not verify_reason.strip():
        return O.refused(
            "bug.fixed",
            "--skip-regression-verify needs --verify-reason saying why; the override is "
            "recorded so a later reader knows the pre-fix check was skipped, and why.",
            id=item,
        )
    refusal, verified, verify_ev, landing = _verified_close(
        repo,
        cfg,
        st,
        item,
        tests,
        verify_regression=verify_regression,
        reason=verify_reason,
        mutation=mutation,
        landed=landed,
    )
    if refusal is not None:
        return refusal
    payload = {
        "regression_test": regression_test,
        "regression_tests": tests,
        "lesson": lesson,
        "regression_verified": verified,
        **({"regression_verify": verify_ev} if verify_ev else {}),
        **({"changelog": entry} if entry else {}),
    }
    dropped = _record_fixed(log, item, payload, landing)
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
        **({"landed": landing[0], **dropped} if landing else {}),
        # The lesson that now holds the text: the new one, or the one it was added to.
        lesson_captured=capture.get("captured") or capture.get("extended", ""),
        lesson_capture=capture,
    )


def _verified_close(
    repo: Path,
    cfg,
    st,
    item: str,
    tests: list[str],
    *,
    verify_regression: bool,
    reason: str,
    mutation: dict[str, str] | None,
    landed: str,
) -> tuple[O.Outcome | None, str, dict[str, Any], tuple[str, str] | None]:
    """`(refusal or None, verdict, evidence, landing)`: the landing the close names and the
    check that the regression test fails without the fix (B-bugfix-verified)."""
    landing, why = _landing_for(repo, cfg, landed, tests, verify_regression)
    if why:
        return O.refused("bug.fixed", why, id=item), "", {}, landing
    verified, verify_ev = _verify_regression(
        repo,
        cfg,
        st,
        item,
        tests,
        verify_regression=verify_regression,
        reason=reason,
        mutation=mutation,
        landing=landing,
    )
    if verified == "needs-fix-task":
        # A gap the caller could close -- by filing a fix task, or naming a landing --
        # is not recorded on a bug left open: the close is REFUSED naming how to give
        # the check its pre-fix source (Bb49d834e90).
        return (
            O.refused("bug.fixed", _needs_fix_task_refusal(item, verify_ev), id=item),
            "",
            {},
            landing,
        )
    if verified in ("passed-on-prefix", "failed-on-fix"):
        return (
            O.refused(
                "bug.fixed",
                f"cannot close {item}: {verify_ev.get('reason', '')}. Name a test that FAILS "
                f"without the fix, or close with --skip-regression-verify --verify-reason "
                f"'<why>' (recorded).",
                id=item,
            ),
            "",
            {},
            landing,
        )
    return None, verified, verify_ev, landing


def _landing_for(
    repo: Path, cfg, landed: str, tests: list[str], verify_regression: bool
) -> tuple[tuple[str, str] | None, str]:
    """`_landing` of ``--landed`` (none given: no landing, no refusal), plus the rule that
    every named test must be a node id it can run on the two trees; ``(None, refusal)``."""
    if not landed.strip():
        half = _half_node_ids(tests) if verify_regression else []
        return None, (
            f"--regression-test: {'; '.join(repr(h) for h in half)} is not a pytest node id "
            f"(`path/to/test_x.py::test_name`): the file or the test name is missing, so "
            f"nothing could be checked to fail without the fix. Check for an unset variable."
            if half
            else ""
        )
    landing, why = _landing(repo, cfg, landed)
    if why:
        return None, f"--landed: {why}"
    loose = [t for t in tests if not _is_node_id(t)]
    if verify_regression and loose:
        return None, (
            "--landed checks each test on the commit's first parent and on the commit, which "
            "needs pytest node ids (path/to/test_x.py::test_name) in --regression-test; "
            f"{'; '.join(loose)} could not be checked and would close the bug unverified."
        )
    return landing, ""


def _missing_fix_source(rec) -> dict[str, str]:
    """Why an open bug's pre-fix source does not exist, and how to give it one (Bb49d834e90).

    `_fixing_item` is None once it has neither a queued fix task nor a found item with a
    worktree or a landing pair, so nothing names what the fix removed. `bug fixed`
    REFUSES the close (never the silent could-not-run gap below) and names what is
    missing and what would make the check runnable.
    """
    named = (
        f"its fix task ({rec.fix_task}) is not in the queue"
        if rec.fix_task
        else "it has no fix task, and the item that found it offers no worktree"
    )
    return {
        "reason": f"no pre-fix source to compare the test against: {named}",
        "remedy": (
            f"file a fix task (`ddflow bug file-tasks {rec.id}`), claim it, and put the "
            "test and the fix inside its globs; or close with `--landed <sha>` when the "
            "fix already reached the base"
        ),
    }


def _needs_fix_task_refusal(item: str, verify_ev: dict[str, Any]) -> str:
    """The `bug fixed` refusal for a bug whose pre-fix source does not exist (and no
    ``--landed`` named one): what is missing, and how the caller gives the check its
    source -- never a close with a silent, caller-fixable gap (Bb49d834e90)."""
    return (
        f"cannot close {item}: the test cannot be shown to fail without the fix -- "
        f"{verify_ev.get('reason', '')}. To fix it: {verify_ev.get('remedy', '')}"
    )


def _record_fixed(
    log, item: str, payload: dict[str, Any], landing: tuple[str, str] | None
) -> dict[str, Any]:
    """Append ``bug.fixed`` and, for a landed fix, end its fix task in the same transaction."""
    with log.transaction():
        log.append("bug.fixed", item, {**payload, **({"landed": landing[0]} if landing else {})})
        return _end_fix_task_landed(log, item, landing[0]) if landing else {}


def _lint_exempt_close(
    repo: Path,
    log,
    cfg,
    st,
    item: str,
    lint_exempt: str,
    tests: list[str],
    entry: dict[str, Any],
    mutation: dict[str, str] | None,
    verify_reason: str,
    landed: str,
) -> O.Outcome:
    """The lint-exempt branch of `bug_fixed`, folded out so that function stays under the
    grade-D complexity ratchet (Bab0c83b80c). `--landed` is allowed here: the exemption
    waives the regression test, not the commit reference, so the record can name the
    commit that holds the fix."""
    takes_no = {"a mutation": mutation, "--verify-reason": verify_reason}
    landing = None
    if landed.strip():
        landing, why = _landing(repo, cfg, landed)
        if why:
            return O.refused("bug.fixed", f"--landed: {why}", id=item)
    return _close_lint_exempt(
        log,
        cfg,
        st,
        item,
        lint_exempt,
        tests,
        entry,
        takes_no=[n for n, v in takes_no.items() if v],
        landing=landing,
        record=_record_fixed,
    )


def _landing(repo: Path, cfg, ref: str) -> tuple[tuple[str, str] | None, str]:
    """``((sha, its first parent), "")`` of a commit named by ``--landed``, else
    ``(None, why it cannot be used)``: it must exist, be on the base (a commit on some side
    branch has not landed anywhere), and have a parent to be the pre-fix source."""
    sha = W.rev(repo, ref.strip())
    if not sha:
        return None, f"{ref.strip()!r} names no commit in this repository"
    base = cfg.worktree.base_ref or W.default_branch(repo)
    if not W.git(repo, "merge-base", "--is-ancestor", sha, base).ok:
        return None, f"{sha[:12]} is not on {base!r}: a fix has landed only once it is there"
    parent = W.rev(repo, f"{sha}^1")
    if not parent:
        return None, f"{sha[:12]} has no parent, so there is no source from before the fix"
    return (sha, parent), ""


def _end_fix_task_landed(log, bug: str, sha: str) -> dict[str, Any]:
    """End the bug's fix task, which has nothing left to do, naming the landing. Decided
    from the log as it is NOW, under the lock, as `bug invalid` does."""
    fresh = fold(log.read_all(), strict=False)
    rec = fresh.bugs.get(bug)
    if rec is None:
        return {}
    removed, kept = _drop_fix_task(
        log, fresh, rec, reason=f"the fix already landed in {sha[:12]}", closed="as fixed"
    )
    abandoned = removed if removed and _ended_by_abandon(log, removed) else ""
    task = fresh.items.get(rec.fix_task) if rec.fix_task else None
    holder = task.lease.holder if kept == "held" and task and task.lease else ""
    return {
        "fix_task": rec.fix_task,
        "fix_task_removed": "" if abandoned else removed,
        "fix_task_abandoned": abandoned,
        "fix_task_kept": kept,
        "fix_task_holder": holder,
    }


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


def _has_landing_pair(item: Any) -> bool:
    """Whether ``item`` landed through ddflow's own merge: it records both the target
    before the landing and the landing itself, the pair a pre-fix check runs between."""
    return bool(item is not None and item.landed_before and item.landed_after)


def _fixing_item(st, cfg, bug_id: str) -> Any:
    """The item whose source is the fix: the bug's fix task, else -- for `bug found
    --item X --no-task`, fixed in the commit that found it -- X while it works in a
    worktree, or X landed through ddflow's own merge (a landed_before/landed_after
    pair) named as the pre-fix source (Bb49d834e90). Only then: a landed X recorded
    through a pull request, which keeps only `merged_sha` and no before/after pair,
    may have been merely where an older bug was seen."""
    rec = st.bugs.get(bug_id)
    if rec is None:
        return None
    fix_task = CM.fix_task_for(st, bug_id, cfg)
    if fix_task is not None:
        return fix_task
    found_on = st.items.get(rec.item) if rec.item else None
    if _has_landing_pair(found_on):
        return found_on
    if (
        found_on is not None
        and found_on.worktree
        and not (found_on.landed_after or found_on.merged_sha)
    ):
        return found_on
    return None


def _verify_regression(
    repo: Path,
    cfg,
    st,
    bug_id: str,
    tests: list[str],
    *,
    verify_regression: bool,
    reason: str,
    mutation: dict[str, str] | None = None,
    landing: tuple[str, str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Run a bug's regression test on the pre-fix and fixed trees (B-bugfix-verified).

    Returns (status, evidence). Only pytest NODE IDS are run: a spec or a shell command
    is "not-applicable" (the static resolution already leaves it `unchecked`). A fix
    task that has LANDED is checked against its landing (`landed_before` is the pre-fix
    source, `landed_after` the fixed one), never the base by name, which then holds the
    fix (B0d5253d31f). An unlanded one needs its WORKTREE, the only thing that then names
    a pre-fix source: an unclaimed fix task is "could-not-run", recorded, never
    "verified". A bug with NO source at all -- no fix task, no found item with a worktree
    or a landing pair -- is "needs-fix-task": `bug_fixed` REFUSES the close and the
    reason names how to give the check its source (Bb49d834e90).
    """
    if not verify_regression:
        return "overridden", {"reason": reason.strip()}
    module_tests = [t for t in tests if _is_node_id(t)]
    if not module_tests:
        return "not-applicable", {}

    fx = _fixing_item(st, cfg, bug_id)
    if landing is not None:
        # The caller named the commit that holds the fix: its first parent is the pre-fix
        # source, whatever the fix task did or did not do (B256be593f0).
        status, ev = G.verify_regression_test(
            repo,
            cfg,
            tree=repo,
            base=landing[1],
            tests=module_tests,
            after=landing[0],
            mutation=mutation,
        )
        return _regression_status(status, ev, G)
    if _has_landing_pair(fx):
        # Merged already: the landing's own before and after, never the base by NAME,
        # which now holds the fix -- the driver merges first and completes after
        # (B0d5253d31f). `tree` is unused: the fixed tree is a checkout of `after`.
        status, ev = G.verify_regression_test(
            repo,
            cfg,
            tree=repo,
            base=fx.landed_before,
            tests=module_tests,
            after=fx.landed_after,
            mutation=mutation,
        )
        return _regression_status(status, ev, G)
    if fx is None:
        return "needs-fix-task", _missing_fix_source(st.bugs[bug_id])
    tree = W.load_path(repo, fx.worktree) if fx is not None and fx.worktree else None
    if tree is None:
        return "could-not-run", {
            "reason": "the bug's fix task is not working in a worktree, so there is no "
            "pre-fix source to compare the test against",
            "remedy": "claim the bug's fix task (its worktree is the pre-fix source), or close "
            "after it has merged and run `complete --regression-test`",
        }
    base = fx.base or cfg.worktree.base_ref or ""
    if not base:
        try:
            base = W.default_branch(W.repo_root(tree))
        except W.GitError:
            base = ""
    if not base:
        return "could-not-run", {
            "reason": "no base ref to build the pre-fix tree from",
            "remedy": "set worktree.base_ref (`ddflow config --set worktree.base_ref <branch>`)",
        }
    status, ev = G.verify_regression_test(
        repo, cfg, tree=tree, base=base, tests=module_tests, mutation=mutation
    )
    return _regression_status(status, ev, G)


_REMEDY = (
    "re-run when the machine is quieter, or check the unit_tests command, the fix task's "
    "worktree and the base ref; `--skip-regression-verify --verify-reason` records an override"
)


def could_not_run_text(ev: dict[str, Any]) -> str:
    """The reason (and remedy) a could-not-run check carries, as one line for the CLI."""
    reason = str(ev.get("reason") or "").strip()
    remedy = str(ev.get("remedy") or "").strip()
    return reason + (f" -- to fix: {remedy}" if remedy else "")


def _regression_status(status: str, ev: dict[str, Any], G: Any) -> tuple[str, dict[str, Any]]:
    """`verify_regression_test`'s status as the bug record names it (``G``: the gates
    package, passed in so it is imported once, lazily, by the caller)."""
    # Explicitly, ONE success status: an unrecognized status must never fall through to
    # "verified" -- a bug whose test was never shown to fail-first would be recorded as
    # verified, the vacuous pass this whole feature exists to prevent (rubber_duck #1).
    if status == G.REGRESSION_VERIFIED:
        return "verified", ev
    if status == G.VERIFIED_BY_MUTATION:
        return "verified-by-mutation", ev
    if status == G.REGRESSION_PASSES_ON_PREFIX:
        return "passed-on-prefix", ev
    if status == G.REGRESSION_FAILS_ON_FIX:
        return "failed-on-fix", ev
    if status == G.REGRESSION_COULD_NOT_RUN:
        # Never silent: every could-not-run names its reason, and what would let it run.
        reason = str(ev.get("reason") or "").strip() or "the check ended without a reason"
        return "could-not-run", {**ev, "reason": reason, "remedy": ev.get("remedy") or _REMEDY}
    return "could-not-run", {
        **ev,
        "reason": f"unrecognized verification status {status!r}",
        "remedy": ev.get("remedy") or _REMEDY,
    }

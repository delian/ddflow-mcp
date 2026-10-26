"""B3eeb47ca9b: a bug filed `--item X --no-task` (fixed in X, the commit that found it)
had its regression test recorded `could-not-run` on every close, even from inside X's
worktree: the verification looked for the pre-fix source only through the bug's fix task,
and such a bug has none. X's worktree is that source while X works in it.

Bb49d834e90: the leftover case -- a bug with no fix task whose found item offers NO
worktree -- still closed `could-not-run`, a gap the caller could close by filing a fix
task or naming a landing. Now the source is the found item's landing pair
(landed_before/landed_after) when it has one, and a close with nothing verifiable is
REFUSED, not recorded as a silent gap.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from test_bug_regression_verified import ALWAYS_PASSES, FAILS_FIRST, _commit, _worktree

from ddflow import api
from ddflow.infra.log import EventLog

OK, REFUSED = 0, 3


def _found_in_task(repo: Path) -> Path:
    """Task T1 in a worktree that fixes `src.f()` (1 -> 2) and adds its fail-first test;
    bug B1 filed against T1 with no task of its own."""
    run_cli(repo, "init")
    (repo / "src.py").write_text("def f():\n    return 1\n")
    run_cli(repo, "config", "--set", "gate.unit_tests.command", f"{sys.executable} -m pytest -q")
    _commit(repo, "src, and a runner that can run a node id")
    code, _out, err = run_cli(
        repo, "task", "add", "T1", "--title", "work", "--globs", "src.py,tests/"
    )
    assert code == OK, err
    code, _out, err = run_cli(repo, "claim", "T1")
    assert code == OK, err
    code, _out, err = run_cli(
        repo, "bug", "found", "--id", "B1", "--item", "T1", "--no-task", "--summary", "f is 1"
    )
    assert code == OK, err
    wt = _worktree(repo, "T1")
    (wt / "src.py").write_text("def f():\n    return 2\n")
    (wt / "tests").mkdir(exist_ok=True)
    (wt / "tests" / "test_f.py").write_text(FAILS_FIRST)
    _commit(wt, "fix f")
    return wt


def test_a_no_task_bug_is_verified_against_its_items_worktree(repo):
    _found_in_task(repo)
    out = api.bug_fixed(repo, "B1", regression_test="tests/test_f.py::test_f")
    assert out.exit == OK, out.reason
    assert out.data["regression_verified"] == "verified", out.data


def test_a_no_task_bugs_test_that_passes_before_the_fix_is_refused(repo):
    wt = _found_in_task(repo)
    (wt / "tests" / "test_always.py").write_text(ALWAYS_PASSES)
    _commit(wt, "an always-passing test")
    out = api.bug_fixed(repo, "B1", regression_test="tests/test_always.py::test_always")
    assert out.exit == REFUSED, out
    assert "pre-fix tree" in out.reason


def test_a_bug_with_no_fix_task_and_no_worktree_is_refused_demanding_a_fix_task(repo):
    """Bb49d834e90: nothing names a pre-fix source, so the close must not proceed with a
    recorded gap -- it refuses, naming how to give it one."""
    run_cli(repo, "init")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_x.py").write_text(ALWAYS_PASSES)
    _commit(repo, "a test")
    run_cli(repo, "task", "add", "T2", "--title", "elsewhere", "--globs", "x.py")
    run_cli(repo, "bug", "found", "--id", "B2", "--item", "T2", "--no-task", "--summary", "s")
    out = api.bug_fixed(repo, "B2", regression_test="tests/test_x.py::test_always")
    assert out.exit == REFUSED, out
    assert "fix task" in out.reason, out.reason
    assert not [e for e in EventLog(repo).read_all() if e.kind == "bug.fixed"], "bug closed"


def test_a_no_task_bugs_item_that_landed_through_a_pr_has_no_pair_so_a_fix_task_is_demanded(repo):
    """A pull-request merge records only `merged_sha`: there is no local landed_before/
    landed_after pair, so the close is refused naming a fix task (not a gap recorded on a
    bug the caller could fix) (roborev on f6065500, extended by Bb49d834e90s refusal)."""
    from ddflow.api._base import _load
    from ddflow.api.knowledge import bug_close as BC

    _found_in_task(repo)
    _log, cfg, st = _load(repo, "")
    st.items["T1"].merged_sha = "deadbeef"
    status, ev = BC._verify_regression(
        repo, cfg, st, "B1", ["tests/test_f.py::test_f"], verify_regression=True, reason=""
    )
    assert status == "needs-fix-task", (status, ev)


def test_a_bugs_missing_fix_task_still_falls_back_to_its_found_item(repo):
    """A bug whose fix_task names an id not in the queue keeps the found item as its
    pre-fix source when that item works in a worktree (the fix task string names nothing)."""
    from ddflow.api._base import _load
    from ddflow.api.knowledge import bug_close as BC

    _found_in_task(repo)
    _log, cfg, st = _load(repo, "")
    st.bugs["B1"].fix_task = "fix-gone"
    status, ev = BC._verify_regression(
        repo, cfg, st, "B1", ["tests/test_f.py::test_f"], verify_regression=True, reason=""
    )
    assert status == "verified", (status, ev)


def test_a_no_task_bugs_item_landed_with_a_pair_is_verified_against_it(repo):
    """Bb49d834e90: the found item merged through ddflow -- its landed_before/landed_after
    pair IS the pre-fix source and the fixed tree, so the regression test really runs."""
    from ddflow.api._base import _load
    from ddflow.api.knowledge import bug_close as BC

    wt = _found_in_task(repo)
    fix_sha = subprocess.run(
        ["git", "-C", str(wt), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    base_sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    _log, cfg, st = _load(repo, "")
    st.items["T1"].merged_sha = fix_sha
    st.items["T1"].landed_before = base_sha
    st.items["T1"].landed_after = fix_sha
    # The pair alone must be decisive: the items WORKTREE is gone once it merged, so a
    # state whose worktree records nothing still verifies through landed_before/after.
    st.items["T1"].worktree = ""
    status, ev = BC._verify_regression(
        repo, cfg, st, "B1", ["tests/test_f.py::test_f"], verify_regression=True, reason=""
    )
    assert status == "verified", (status, ev)
    assert ev["prefix_outcome"] == "failed" and ev["fix_outcome"] == "passed", ev

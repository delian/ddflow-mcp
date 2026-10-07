"""B3eeb47ca9b: a bug filed `--item X --no-task` (fixed in X, the commit that found it)
had its regression test recorded `could-not-run` on every close, even from inside X's
worktree: the verification looked for the pre-fix source only through the bug's fix task,
and such a bug has none. X's worktree is that source while X works in it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from test_bug_regression_verified import ALWAYS_PASSES, FAILS_FIRST, _commit, _worktree

from ddflow import api

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


def test_a_no_task_bug_whose_item_has_no_worktree_is_still_could_not_run(repo):
    run_cli(repo, "init")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_x.py").write_text(ALWAYS_PASSES)
    _commit(repo, "a test")
    run_cli(repo, "task", "add", "T2", "--title", "elsewhere", "--globs", "x.py")
    run_cli(repo, "bug", "found", "--id", "B2", "--item", "T2", "--no-task", "--summary", "s")
    out = api.bug_fixed(repo, "B2", regression_test="tests/test_x.py::test_always")
    assert out.exit == OK, out.reason
    assert out.data["regression_verified"] == "could-not-run", out.data


def test_a_no_task_bugs_item_that_landed_through_a_pr_is_not_its_pre_fix_source(repo):
    """A pull-request merge records only `merged_sha`: the item's base then holds the fix,
    and its worktree must not be taken as the pre-fix source (roborev on f6065500)."""
    from ddflow.api._base import _load
    from ddflow.api.knowledge import bug_close as BC

    _found_in_task(repo)
    _log, cfg, st = _load(repo, "")
    st.items["T1"].merged_sha = "deadbeef"
    status, ev = BC._verify_regression(
        repo, cfg, st, "B1", ["tests/test_f.py::test_f"], verify_regression=True, reason=""
    )
    assert status == "could-not-run", (status, ev)

"""B-bug-fixed-other-item-unverified: `bug fixed` looked only for the bug's OWN fix task's
worktree, so a fix made in the item the bug was found on — while that bug's own
(auto-filed) fix task stayed unclaimed — was recorded `could-not-run` and closed the bug
unverified. The found item's worktree is the fix source while it works in one, even when
the bug also has its own (unclaimed) fix task.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from test_bug_regression_verified import FAILS_FIRST, _commit, _worktree

from ddflow import api

OK = 0


def _fix_made_in_the_found_item(repo: Path) -> Path:
    """Task T1 works in a worktree and fixes `src.f()` (1 -> 2) with its fail-first test;
    bug B1 is filed against T1 WITHOUT `--no-task`, so it gets its own auto fix task
    `fix-B1` that stays UNCLAIMED — the fix lives in T1's worktree, not fix-B1's."""
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
    # No `--no-task`: B1 files its own fix task (fix-B1), which is never claimed.
    code, _out, err = run_cli(
        repo, "bug", "found", "--id", "B1", "--item", "T1", "--summary", "f is 1"
    )
    assert code == OK, err
    wt = _worktree(repo, "T1")
    (wt / "src.py").write_text("def f():\n    return 2\n")
    (wt / "tests").mkdir(exist_ok=True)
    (wt / "tests" / "test_f.py").write_text(FAILS_FIRST)
    _commit(wt, "fix f")
    return wt


def test_a_fix_in_the_items_worktree_verifies_even_when_the_bug_has_its_own_unclaimed_fix_task(repo):
    _fix_made_in_the_found_item(repo)
    out = api.bug_fixed(repo, "B1", regression_test="tests/test_f.py::test_f")
    assert out.exit == OK, out.reason
    assert out.data["regression_verified"] == "verified", out.data

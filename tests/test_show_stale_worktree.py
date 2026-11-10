"""`ddflow show` must not present a worktree and branch that no longer exist (Bf3c93a0759).

An open, unleased item keeps its recorded worktree and branch fields after the tree and
branch are removed by hand (a crash, a manual `git worktree remove`, a `cleanup`), and
`show` printed them anyway, so a reader believed parked work was still there."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import run_cli
from helpers import git as _git


def test_show_flags_a_recorded_worktree_and_branch_that_no_longer_exist(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "a.py")
    code, _, err = run_cli(repo, "claim", "T1", agent="worker")
    assert code == 0, err
    _, shown, _ = run_cli(repo, "--json", "show", "T1")
    info = json.loads(shown)
    tree = Path(info["worktree"])
    tree = tree if tree.is_absolute() else repo / tree
    branch = info["branch"]
    assert tree.exists(), "fixture: claimed T1 produced no worktree"
    run_cli(repo, "release", "T1", agent="worker")

    # The tree and branch go away by hand (as a crashed lane's cleanup does), the record
    # is untouched: `show` must not let a reader believe parked work is still there.
    _git(repo, "worktree", "remove", "--force", str(tree))
    _git(repo, "branch", "-D", branch)

    code, out, err = run_cli(repo, "show", "T1")
    assert code == 0, err
    assert "no such worktree or branch" in out, f"show names a gone worktree/branch: {out}"

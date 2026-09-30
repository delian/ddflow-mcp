"""A tree whose item is merged and released can be claimed for the next item (B0ff09a29a5).

One tree per open item is the rule, because two items sharing a tree cannot be merged or
recovered apart. It refused even when the bound item's work was ALL on the base already:
B-release-every-push was merged, its lease released, and it was open only for a review
the operator had deferred -- yet `claim` of the next item in that tree was refused, and
the way out it offered (`--no-worktree`) is what then made `merge` and `review` misbehave.

The protection is for WORK: a released item whose tree still holds unmerged commits or
uncommitted changes is exactly what must not be co-opted, and still is not.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


def _git(where: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(where), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _first_item_worked_in_the_harness_tree(repo: Path) -> Path:
    run_cli(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    tree = repo.parent / "agent-tree"
    _git(repo, "worktree", "add", "-q", str(tree), "-b", "agent-work")
    run_cli(repo, "task", "add", "T1", "--title", "first", "--globs", "a.py")
    run_cli(repo, "task", "add", "T2", "--title", "second", "--globs", "b.py")
    code, out, err = run_cli(tree, "claim", "T1")
    assert code == OK and "adopted" in out, out + err
    (tree / "a.py").write_text("a = 1\n")
    _git(tree, "add", "a.py")
    _git(tree, "commit", "-qm", "T1")
    return tree


def test_a_tree_whose_item_is_merged_and_released_takes_the_next_claim(repo):
    tree = _first_item_worked_in_the_harness_tree(repo)
    code, out, err = run_cli(tree, "merge", "T1")
    assert code == OK, out + err
    assert run_cli(tree, "release", "T1")[0] == OK
    code, out, err = run_cli(tree, "claim", "T2")
    assert code == OK, out + err


def test_unmerged_commits_still_hold_the_tree(repo):
    tree = _first_item_worked_in_the_harness_tree(repo)
    assert run_cli(tree, "release", "T1")[0] == OK
    code, out, err = run_cli(tree, "claim", "T2")
    assert code == REFUSED, out + err
    assert "T1" in out + err


def test_uncommitted_changes_still_hold_the_tree(repo):
    tree = _first_item_worked_in_the_harness_tree(repo)
    assert run_cli(tree, "merge", "T1")[0] == OK
    assert run_cli(tree, "release", "T1")[0] == OK
    (tree / "a.py").write_text("a = 2  # more of T1's work, not committed\n")
    code, out, err = run_cli(tree, "claim", "T2")
    assert code == REFUSED, out + err


def test_a_live_lease_still_holds_the_tree(repo):
    tree = _first_item_worked_in_the_harness_tree(repo)
    assert run_cli(tree, "merge", "T1")[0] == OK
    code, out, err = run_cli(tree, "claim", "T2")
    assert code == REFUSED, out + err

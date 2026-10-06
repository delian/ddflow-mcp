"""Bccf6d1aec7: a delta review after `git merge main` sent main's incoming commits too.

The delta diffed from the last reviewed head to the branch tip, a range that holds every
commit the merge brought in from main -- 560-695k characters of other items' code,
reviewed elsewhere already. The delta is the item's OWN change since the reviewed head:
main's incoming work is taken as given (the reviewed head merged with what came in),
and only what the branch did on top of that is sent.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from ddflow.api import review as RV


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(cwd: Path, path: str, text: str, msg: str) -> str:
    (cwd / path).parent.mkdir(parents=True, exist_ok=True)
    (cwd / path).write_text(text)
    _git(cwd, "add", path)
    _git(cwd, "commit", "-qm", msg)
    return _git(cwd, "rev-parse", "HEAD")


@pytest.fixture
def merged(repo: Path, tmp_path: Path):
    """main, an item branch in its own worktree reviewed at `head`, then main moves on
    (a large unrelated change), the item merges main, and commits one more change."""
    tree = tmp_path / "item"
    _git(repo, "worktree", "add", "-q", "-b", "item", str(tree))
    head = _commit(tree, "own.py", "x = 1\n", "item: first")
    _commit(repo, "theirs.py", "".join(f"y{i} = {i}\n" for i in range(200)), "main: other item")
    _git(tree, "merge", "-q", "--no-edit", "main")
    _commit(tree, "own.py", "x = 2\n", "item: after review")
    return repo, tree, head


def _item(tree: Path, branch: str = "item"):
    return SimpleNamespace(id="T1", worktree=str(tree), branch=branch)


def test_the_delta_from_the_worktree_is_the_items_own_change(merged):
    repo, tree, head = merged
    diff = RV._delta_diff(repo, _item(tree), "", head)
    assert "theirs.py" not in diff, "main's incoming commit was sent to the reviewer"
    assert "own.py" in diff and "+x = 2" in diff


def test_the_delta_of_a_named_branch_is_the_items_own_change(merged):
    repo, _tree, head = merged
    diff = RV._delta_diff(repo, _item(Path("/nonexistent")), "item", head)
    assert "theirs.py" not in diff
    assert "+x = 2" in diff


def test_the_delta_counts_only_the_items_own_commits(merged):
    repo, tree, head = merged
    assert RV._commits_since(repo, _item(tree), "", head) == 2  # its commit + the merge


def test_a_change_made_while_resolving_the_merge_is_sent(merged, tmp_path):
    """What the item did to main's file in the merge is its own change."""
    repo, tree, head = merged
    (tree / "theirs.py").write_text("y0 = 'edited by the item'\n")
    _git(tree, "commit", "-qam", "item: touch theirs")
    diff = RV._delta_diff(repo, _item(tree), "", head)
    assert "edited by the item" in diff


def test_without_a_main_merge_the_delta_is_unchanged(repo, tmp_path):
    tree = tmp_path / "item"
    _git(repo, "worktree", "add", "-q", "-b", "item", str(tree))
    head = _commit(tree, "own.py", "x = 1\n", "first")
    _commit(tree, "own.py", "x = 2\n", "second")
    diff = RV._delta_diff(repo, _item(tree), "", head)
    assert "+x = 2" in diff and "-x = 1" in diff


def test_a_conflicted_merge_still_sends_none_of_mains_other_work(repo, tmp_path):
    """Reviewed head and main both changed one file; the item resolved the conflict. The
    two do not merge cleanly again, so the delta is the item's whole own change on top of
    main -- still without main's unrelated work."""
    tree = tmp_path / "item"
    _commit(repo, "shared.py", "v = 0\n", "base")
    _git(repo, "worktree", "add", "-q", "-b", "item", str(tree))
    head = _commit(tree, "shared.py", "v = 'item'\n", "item: change shared")
    _commit(repo, "shared.py", "v = 'main'\n", "main: change shared")
    _commit(repo, "theirs.py", "unrelated = 1\n", "main: other item")
    subprocess.run(["git", "-C", str(tree), "merge", "-q", "main"], capture_output=True)
    (tree / "shared.py").write_text("v = 'resolved'\n")
    _git(tree, "commit", "-qam", "item: resolve")
    diff = RV._delta_diff(repo, _item(tree), "", head)
    assert "theirs.py" not in diff
    assert "resolved" in diff

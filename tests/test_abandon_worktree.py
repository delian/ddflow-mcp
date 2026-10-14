"""Finishing an item by abandon or remove disposes its own worktree and branch (B79319c6ada),
and `complete` does too once the caller is out of the tree (B4757c428f4).

`merge` removes a clean tree and deletes its merged branch; `abandon` and `remove` only
recorded the event, so an abandoned item kept both forever and the doctor listed them as
orphans. Only what `dispose_tree` allows goes: nothing is removed that holds uncommitted
files or unmerged commits, and the directory the caller stands in is kept.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import lifecycle as LC
from ddflow.infra.log import EventLog


def _seed(repo: Path, item: str) -> Path:
    run_cli(repo, "init")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "Phase one"})
    log.append("task.added", item, {"parent": "P1", "title": item, "globs": [f"src/{item}/*"]})
    out = LC.claim(repo, item, agent="worker")
    assert out.exit == 0, out.reason
    return Path(out.data["worktree"])


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def _branches(repo: Path) -> list[str]:
    return _git("-C", str(repo), "branch", "--format=%(refname:short)").split()


def _commit_in(tree: Path, name: str) -> None:
    (tree / name).write_text("work\n")
    _git("-C", str(tree), "add", name)
    _git("-C", str(tree), "-c", "user.email=t@e.com", "-c", "user.name=T", "commit", "-qm", "wip")


def test_abandon_removes_a_clean_tree_and_its_branch(repo):
    tree = _seed(repo, "T1")
    out = LC.abandon(repo, "T1", reason="not needed", agent="worker")
    assert out.exit == 0, out.reason
    assert out.data["worktree_removed"] is True
    assert not tree.exists()
    assert "ddflow/T1" not in _branches(repo)


def test_abandon_keeps_a_tree_with_unmerged_commits_and_says_so(repo):
    tree = _seed(repo, "T2")
    _commit_in(tree, "work.py")
    out = LC.abandon(repo, "T2", reason="superseded", agent="worker")
    assert out.exit == 0, out.reason
    assert out.data["worktree_removed"] is False
    assert "unmerged commit" in out.data["kept_reason"] and "ddflow/T2" in out.data["kept_reason"]
    assert tree.exists() and "ddflow/T2" in _branches(repo)


def test_abandon_keeps_a_tree_with_uncommitted_files(repo):
    tree = _seed(repo, "T3")
    (tree / "draft.py").write_text("x = 1\n")
    out = LC.abandon(repo, "T3", reason="superseded", agent="worker")
    assert "uncommitted" in out.data["kept_reason"]
    assert tree.exists()


def test_abandon_keeps_the_tree_the_caller_stands_in(repo):
    tree = _seed(repo, "T4")
    out = LC.abandon(repo, "T4", reason="no", agent="worker", shell_cwd=tree)
    assert "standing in it" in out.data["kept_reason"]
    assert tree.exists()


def test_remove_disposes_a_clean_tree_too(repo):
    tree = _seed(repo, "T5")
    out = LC.remove(repo, "T5", reason="dropped", agent="worker")
    assert out.exit == 0, out.reason
    assert out.data["worktree_removed"] is True
    assert not tree.exists() and "ddflow/T5" not in _branches(repo)


def test_the_knob_turns_disposal_off(repo):
    tree = _seed(repo, "T6")
    assert run_cli(repo, "config", "worktree.dispose_finished", "false")[0] == 0
    out = LC.abandon(repo, "T6", reason="no", agent="worker")
    assert out.data["worktree_removed"] is False and out.data["kept_reason"] == ""
    assert tree.exists()


def test_the_cli_says_what_became_of_the_tree(repo):
    _seed(repo, "T7")
    code, out, err = run_cli(repo, "abandon", "T7", "--reason", "no")
    assert code == 0, err
    assert "worktree and branch removed" in out, out


def test_a_branch_whose_tree_was_removed_by_hand_is_still_deleted_when_merged(repo):
    tree = _seed(repo, "T8")
    _git("-C", str(repo), "worktree", "remove", "--force", str(tree))
    assert "ddflow/T8" in _branches(repo)
    out = LC.abandon(repo, "T8", reason="no", agent="worker")
    assert out.exit == 0, out.reason
    assert "ddflow/T8" not in _branches(repo)
    assert "its worktree was already gone" in out.data["kept_reason"]


def test_an_unmerged_branch_without_a_tree_is_kept_and_named(repo):
    tree = _seed(repo, "T9")
    _commit_in(tree, "work.py")
    _git("-C", str(repo), "worktree", "remove", "--force", str(tree))
    out = LC.abandon(repo, "T9", reason="no", agent="worker")
    assert "ddflow/T9" in _branches(repo)
    assert "ddflow/T9 kept" in out.data["kept_reason"]


def test_remove_on_merge_false_keeps_trees_for_complete_abandon_and_remove_too(repo):
    """The standing 'keep my trees for post-mortems' setting is honoured by every way an
    item ends, not just merge."""
    tree = _seed(repo, "T10")
    assert run_cli(repo, "config", "worktree.remove_on_merge", "false")[0] == 0
    out = LC.abandon(repo, "T10", reason="no", agent="worker")
    assert out.data["worktree_removed"] is False and tree.exists()

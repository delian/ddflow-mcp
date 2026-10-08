"""One worktree lifecycle (D-unify, B-uni-tree-lifecycle): the scratch tree."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ddflow.infra import worktree as W


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.org")
    _git(r, "config", "user.name", "t")
    (r / "a.txt").write_text("a\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "one")
    _git(r, "branch", "side")
    return r


def _registered(repo: Path) -> list[str]:
    return [
        line.split(" ", 1)[1]
        for line in _git(repo, "worktree", "list", "--porcelain").splitlines()
        if line.startswith("worktree ")
    ]


def test_a_scratch_tree_exists_inside_the_block_and_is_gone_after(repo):
    with W.scratch_tree(repo, "side", prefix="u-") as s:
        assert s.path is not None and (s.path / "a.txt").is_file()
        assert s.path.name.startswith("u-")
        assert str(s.path) in _registered(repo)
        seen = s.path
    assert not seen.exists()
    assert _registered(repo) == [str(repo)]


def test_it_is_removed_and_pruned_when_the_body_raises(repo):
    with pytest.raises(RuntimeError), W.scratch_tree(repo, "side", detach=True) as s:
        assert s.path is not None
        (s.path / "dirty.txt").write_text("uncommitted\n")  # --force removes a dirty tree
        raise RuntimeError("boom")
    assert _registered(repo) == [str(repo)]


def test_detach_lets_it_hold_a_branch_checked_out_elsewhere(repo):
    with W.scratch_tree(repo, "main", detach=True) as s:  # main is the primary's branch
        assert s.path is not None
    with W.scratch_tree(repo, "main") as plain:  # a branch can be in one tree only
        assert plain.path is None and plain.error


def test_a_ref_git_refuses_yields_no_path_and_the_reason(repo, tmp_path):
    with W.scratch_tree(repo, "no-such-ref", under=tmp_path) as s:
        assert s.path is None
        assert s.error and s.add.code != 0
    assert [p.name for p in tmp_path.iterdir()] == ["repo"]  # no scratch directory left


def test_under_puts_the_tree_in_that_directory(repo, tmp_path):
    under = tmp_path / "scratch"
    under.mkdir()
    with W.scratch_tree(repo, "side", under=under, prefix=".merge-") as s:
        assert s.path is not None and s.path.parent == under
    assert list(under.iterdir()) == []


def test_a_stale_registration_is_pruned_even_when_the_tree_was_deleted_by_the_body(repo):
    import shutil

    with W.scratch_tree(repo, "side") as s:
        assert s.path is not None
        shutil.rmtree(s.path)  # the body removed the directory itself
    assert _registered(repo) == [str(repo)]

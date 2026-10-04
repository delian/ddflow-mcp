"""Every agent's worktrees live in .ddflow/worktrees inside the project (D-worktree-home)."""

from __future__ import annotations

import subprocess

from conftest import run_cli

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import adopt as AD


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


def test_the_default_root_is_inside_the_project():
    assert Config().worktree.root == ".ddflow/worktrees"


def _adopted(repo):
    assert run_cli(repo, "init")[0] == 0
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "adopt ddflow")
    assert run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "a.py")[0] == 0


def _untracked(repo):
    return _git(repo, "status", "--porcelain", "--untracked-files=all")


def test_a_claim_creates_its_tree_under_the_project_and_git_ignores_it(repo):
    _adopted(repo)
    assert run_cli(repo, "claim", "T1")[0] == 0
    wt = fold(EventLog(repo).read_all(), strict=False).items["T1"].worktree
    assert (repo / wt).resolve() == (repo / ".ddflow" / "worktrees" / "T1").resolve()
    (repo / wt / "a.py").write_text("x = 1\n")
    # the main checkout sees neither the tree nor its files
    status = _untracked(repo)
    assert "worktrees" not in status and "a.py" not in status, status


def test_a_project_with_the_old_ddflow_gitignore_still_hides_the_new_tree(repo):
    """Adopted before the move: .ddflow/.gitignore has no worktrees/ line yet."""
    _adopted(repo)
    gi = repo / ".ddflow" / ".gitignore"
    gi.write_text(gi.read_text().replace("/worktrees/\n", "").replace("/runs/\n", ""))
    _git(repo, "commit", "-qam", "old ignore file")
    assert run_cli(repo, "claim", "T1")[0] == 0
    status = _untracked(repo)
    assert "worktrees" not in status, status


def test_the_ddflow_gitignore_covers_worktrees_and_runs():
    lines = AD.DDFLOW_GITIGNORE.splitlines()
    assert "/worktrees/" in lines and "/runs/" in lines


def test_a_tree_recorded_under_the_former_root_is_still_found(repo):
    """Changing the default must not strand trees created under ../.ddflow-worktrees."""
    _adopted(repo)
    (repo / ".ddflow" / "config.toml").write_text('[worktree]\nroot = "../.ddflow-worktrees"\n')
    assert run_cli(repo, "claim", "T1")[0] == 0
    old = (repo.parent / ".ddflow-worktrees" / "T1").resolve()
    assert old.is_dir()
    (repo / ".ddflow" / "config.toml").write_text("")  # back to the new default
    assert run_cli(repo, "release", "T1")[0] == 0
    code, _out, err = run_cli(repo, "claim", "T1")
    assert code == 0, err
    item = fold(EventLog(repo).read_all(), strict=False).items["T1"]
    assert (repo / item.worktree).resolve() == old
    assert not (repo / ".ddflow" / "worktrees" / "T1").exists()

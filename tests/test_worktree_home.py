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


def test_a_claim_creates_its_tree_under_the_project_and_git_ignores_it(repo):
    run_cli(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "adopt ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "a.py")
    assert run_cli(repo, "claim", "T1")[0] == 0
    wt = fold(EventLog(repo).read_all(), strict=False).items["T1"].worktree
    assert (repo / wt).resolve() == (repo / ".ddflow" / "worktrees" / "T1").resolve()
    (repo / wt / "a.py").write_text("x = 1\n")
    # the main checkout sees neither the tree nor its files
    status = _git(repo, "status", "--porcelain", "--untracked-files=all")
    assert "worktrees" not in status and "a.py" not in status, status


def test_the_ddflow_gitignore_covers_worktrees_and_runs():
    lines = AD.DDFLOW_GITIGNORE.splitlines()
    assert "worktrees/" in lines and "runs/" in lines


def test_a_tree_recorded_under_the_former_root_is_still_found(repo, tmp_path):
    """Changing the default must not strand trees created under ../.ddflow-worktrees."""
    run_cli(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "adopt ddflow")
    (repo / ".ddflow" / "config.toml").write_text('[worktree]\nroot = "../.ddflow-worktrees"\n')
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "a.py")
    assert run_cli(repo, "claim", "T1")[0] == 0
    (repo / ".ddflow" / "config.toml").write_text("")  # back to the new default
    assert run_cli(repo, "release", "T1")[0] == 0
    code, out, err = run_cli(repo, "claim", "T1")
    assert code == 0, err
    assert ".ddflow-worktrees" in out  # the recorded tree, not a new one

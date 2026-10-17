"""A refused `claim` names the exact next command (DX-worktree-claim-ready), and an item
blocked by another agent's lease on ITSELF is not said to be "waiting for T1" (the folded
DX-self-wait-wording)."""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git as _git


def _seed(repo: Path) -> None:
    run_cli(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    run_cli(repo, "task", "add", "T2", "--globs", "b.py")


def test_an_item_held_by_another_agent_is_not_waiting_for_itself(repo):
    _seed(repo)
    assert run_cli(repo, "claim", "T1", "--no-worktree", agent="a")[0] == 0
    code, out, err = run_cli(repo, "claim", "T1", "--no-worktree", agent="b")
    text = out + err
    assert code == 3, text
    assert "T1 is waiting for T1" not in text, text
    assert "waiting for its lease held by a" in text, text


def test_a_tree_bound_to_another_item_names_the_commands_to_get_past_it(repo):
    _seed(repo)
    tree = repo.parent / "harness-tree"
    _git(repo, "worktree", "add", "-q", str(tree), "-b", "harness")
    assert run_cli(tree, "claim", "T1", agent="a")[0] == 0
    code, out, err = run_cli(tree, "claim", "T2", agent="a")
    text = out + err
    assert code == 3, text
    assert "ddflow complete T1" in text and "ddflow claim T2 --no-worktree" in text, text


def test_an_occupied_tree_path_names_the_removal_command(repo):
    _seed(repo)
    path = repo / ".ddflow" / "worktrees" / "T1"
    _git(repo, "worktree", "add", "-q", str(path), "-b", "somewhere-else")
    code, out, err = run_cli(repo, "claim", "T1", agent="a")
    text = out + err
    assert code == 3, text
    assert "git worktree remove" in text and "ddflow claim T1" in text, text
    # the tree may be dirty, so removal can fail: say how to look first and the alternative
    assert f"git -C {path} status" in text, text
    assert "tree of your own" in text, text


def test_another_agents_expired_lease_names_the_one_line_takeover(repo, monkeypatch):
    run_cli(repo, "init")
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(re.sub(r"(?m)^\[lease\]$", "[lease]\ngrace_s = 0", cfg.read_text(), count=1))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    monkeypatch.setenv("DDFLOW_LEASE_TTL_S", "1")
    assert run_cli(repo, "claim", "T1", "--no-worktree", agent="a")[0] == 0
    monkeypatch.delenv("DDFLOW_LEASE_TTL_S")
    time.sleep(2.5)
    code, out, err = run_cli(repo, "claim", "T1", "--no-worktree", agent="b")
    text = out + err
    assert code == 3, text
    assert "ddflow recover --item T1`, then `ddflow claim T1 --force" in text, text


def _grace0(repo: Path) -> None:
    run_cli(repo, "init")
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(re.sub(r"(?m)^\[lease\]$", "[lease]\ngrace_s = 0", cfg.read_text(), count=1))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")


def test_the_same_agent_reclaiming_its_live_lease_is_not_a_wait(repo):
    _grace0(repo)
    assert run_cli(repo, "claim", "T1", "--no-worktree", agent="a")[0] == 0
    code, out, err = run_cli(repo, "claim", "T1", "--no-worktree", agent="a")
    assert code == 0 and "waiting" not in out + err, out + err


def test_the_same_agent_resumes_its_own_lapsed_lease_without_force(repo, monkeypatch):
    _grace0(repo)
    monkeypatch.setenv("DDFLOW_LEASE_TTL_S", "1")
    assert run_cli(repo, "claim", "T1", "--no-worktree", agent="a")[0] == 0
    monkeypatch.delenv("DDFLOW_LEASE_TTL_S")
    time.sleep(2.5)
    code, out, err = run_cli(repo, "claim", "T1", "--no-worktree", agent="a")
    assert code == 0 and "--force" not in out + err, out + err

"""A reviewer tool that changes git state is not a review (bug B5ce30dd94d).

kilo (under roborev) popped an unrelated stash, and another lane's stash was applied
inside a worktree. The stash list is shared by every worktree, so `git stash apply`
from a reviewer rewrites the working tree of whoever ran it. ddflow compares HEAD, the
index/working tree and the stash list before and after a reviewer runs.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ddflow.services import gates as G
from ddflow.services import review as R
from ddflow.services.gates.reviewers import git_state, run_watching_git


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


def _repo_with_stash(tmp_path: Path) -> Path:
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "f.txt").write_text("one\n")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-qm", "init")
    (repo / "f.txt").write_text("two\n")
    _git(repo, "stash", "push", "-q", "-m", "someone-elses-wip")
    return repo


def test_git_state_sees_a_stash_apply(tmp_path):
    repo = _repo_with_stash(tmp_path)
    before = git_state(repo)
    _git(repo, "stash", "apply", "-q")
    assert git_state(repo) != before


def test_a_standards_gate_whose_tool_applies_a_stash_is_unavailable(tmp_path):
    repo = _repo_with_stash(tmp_path)
    gdef = G.GateDef(id="standards", command="git stash apply -q; echo 'no findings'")
    outcome, ev = run_watching_git(gdef, repo, lambda: G.run_command_gate(gdef, repo))
    assert outcome == "unavailable"
    assert "git state" in ev["reason"] and "git stash apply" in ev["reason"]
    assert ev["git_state_changed"]["before"] != ev["git_state_changed"]["after"]


def test_a_standards_gate_that_leaves_git_alone_still_passes(tmp_path):
    repo = _repo_with_stash(tmp_path)
    gdef = G.GateDef(id="standards", command="git status >/dev/null; echo ok")
    outcome, ev = run_watching_git(gdef, repo, lambda: G.run_command_gate(gdef, repo))
    assert outcome == "passed", ev
    assert "git_state_changed" not in ev


def test_a_non_reviewer_gate_may_write_the_tree(tmp_path):
    repo = _repo_with_stash(tmp_path)
    gdef = G.GateDef(id="unit_tests", command="git stash apply -q")
    outcome, ev = run_watching_git(gdef, repo, lambda: G.run_command_gate(gdef, repo))
    assert outcome == "passed", ev


def test_a_command_reviewer_that_applies_a_stash_did_not_review(tmp_path, monkeypatch):
    repo = _repo_with_stash(tmp_path)
    monkeypatch.chdir(repo)
    rev = R.Reviewer(name="r", kind="command", command="git stash apply -q; echo 'LGTM'")
    out, err = R._chat(rev, "sys", "user", 30)
    assert out == "" and "git state" in err


def test_git_state_sees_an_untracked_file_rewritten(tmp_path):
    repo = _repo_with_stash(tmp_path)
    (repo / "scratch.txt").write_text("A")
    before = git_state(repo)
    (repo / "scratch.txt").write_text("B")
    assert git_state(repo) != before


def test_a_repository_the_tool_broke_is_a_change_not_a_pass():
    from ddflow.services.gates.reviewers import git_state_change

    state = {"head": "a", "status": "b"}
    assert "unreadable" in git_state_change(state, None)
    assert git_state_change(None, state) == "" and git_state_change(state, state) == ""


def test_git_state_sees_head_detached_at_the_same_commit(tmp_path):
    repo = _repo_with_stash(tmp_path)
    before = git_state(repo)
    _git(repo, "checkout", "-q", "--detach")
    assert git_state(repo) != before

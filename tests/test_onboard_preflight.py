"""Onboarding stage 2: every leftover class, and the removal that is safe for each.

The preflight's value is in what it REFUSES to touch: a matching commit message is not
"merged", a dirty worktree holds work even when its branch landed, a live harness lock
is a session's working directory, and a stash belongs to whoever made it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.api import onboard as api_onboard
from ddflow.services import onboard as ON

OK, NOTHING = 0, 2


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _commit(repo: Path, name: str) -> None:
    (repo / name).write_text("x\n")
    _git(repo, "add", name)
    _git(repo, "commit", "-qm", f"add {name}")


def _merged_branch(repo: Path, name: str = "landed") -> str:
    _git(repo, "checkout", "-qb", name)
    _commit(repo, f"{name}.py")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--ff-only", name)
    return name


def _unique_branch(repo: Path, name: str = "wip") -> str:
    _git(repo, "checkout", "-qb", name)
    _commit(repo, f"{name}.py")
    _git(repo, "checkout", "-q", "main")
    return name


def _worktree(repo: Path, tmp_path: Path, branch: str, name: str = "wt") -> Path:
    path = tmp_path / name
    _git(repo, "worktree", "add", "-q", str(path), branch)
    return path


def _item(items, kind: str, needle: str):
    found = [i for i in items if i.kind == kind and needle in i.name]
    assert len(found) == 1, f"expected one {kind} matching {needle!r}, got {items}"
    return found[0]


def test_a_clean_repository_has_nothing_to_report(repo):
    assert ON.preflight(repo) == []
    assert "nothing left behind" in ON.render([])


def test_a_merged_branch_is_proposed_for_deletion(repo):
    _merged_branch(repo)
    item = _item(ON.preflight(repo), "branch", "landed")
    assert (item.state, item.action) == ("merged", "remove")
    assert ON.apply(repo) == ["deleted branch landed"]
    assert not _branch_exists(repo, "landed")


def test_an_unmerged_branch_is_kept_with_its_commits(repo):
    _unique_branch(repo)
    item = _item(ON.preflight(repo), "branch", "wip")
    assert (item.state, item.action) == ("unique", "keep")
    assert "1 commit(s) not in main" in item.detail and "add wip.py" in item.detail
    assert ON.apply(repo) == []
    assert _branch_exists(repo, "wip")


def test_a_merged_clean_worktree_is_removed_and_its_branch_deleted(repo, tmp_path):
    _merged_branch(repo)
    path = _worktree(repo, tmp_path, "landed")
    item = _item(ON.preflight(repo), "worktree", str(path))
    assert (item.state, item.action, item.branch) == ("merged", "remove", "landed")
    assert ON.apply(repo) == [f"removed worktree {path} and its branch landed"]
    assert not path.exists() and not _branch_exists(repo, "landed")


def test_a_dirty_worktree_holding_work_is_never_removed(repo, tmp_path):
    _merged_branch(repo)
    path = _worktree(repo, tmp_path, "landed")
    (path / "wip.py").write_text("uncommitted\n")
    item = _item(ON.preflight(repo), "worktree", str(path))
    assert (item.state, item.action) == ("dirty", "keep")
    assert "1 uncommitted file(s)" in item.detail
    assert ON.apply(repo) == []
    assert (path / "wip.py").read_text() == "uncommitted\n"


def test_a_stale_lock_is_unlocked_and_removed(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(ON, "alive", lambda pid: False)
    _merged_branch(repo)
    path = _worktree(repo, tmp_path, "landed")
    _git(repo, "worktree", "lock", "--reason", "agent session (pid 4242)", str(path))
    item = _item(ON.preflight(repo), "worktree", str(path))
    assert (item.state, item.action) == ("locked-stale", "remove")
    assert "process 4242 is gone" in item.detail
    assert ON.apply(repo) == [f"removed worktree {path} and its branch landed"]
    assert not path.exists()


def test_a_live_lock_is_never_touched(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(ON, "alive", lambda pid: True)
    _merged_branch(repo)
    path = _worktree(repo, tmp_path, "landed")
    _git(repo, "worktree", "lock", "--reason", "agent session (pid 4242)", str(path))
    item = _item(ON.preflight(repo), "worktree", str(path))
    assert (item.state, item.action) == ("locked", "keep")
    assert "process 4242 is alive" in item.detail
    assert ON.apply(repo) == []
    assert path.exists()


def test_a_lock_without_a_pid_cannot_be_judged(repo, tmp_path):
    _merged_branch(repo)
    path = _worktree(repo, tmp_path, "landed")
    _git(repo, "worktree", "lock", "--reason", "the agent harness", str(path))
    item = _item(ON.preflight(repo), "worktree", str(path))
    assert (item.state, item.action) == ("locked", "keep")
    assert "cannot tell" in item.detail
    assert ON.apply(repo) == [] and path.exists()


def test_a_stash_is_reported_but_never_dropped(repo):
    (repo / "README.md").write_text("# changed\n")
    _git(repo, "stash", "push", "-qm", "stashed work")
    item = _item(ON.preflight(repo), "stash", "stash@{0}")
    assert (item.state, item.action) == ("unique", "keep")
    assert "stashed work" in item.detail and "never dropped" in item.detail
    assert ON.apply(repo) == []
    listed = subprocess.run(
        ["git", "-C", str(repo), "stash", "list"], capture_output=True, text=True
    ).stdout
    assert "stashed work" in listed


def test_an_unmerged_remote_branch_is_reported_and_left_alone(repo, tmp_path):
    _unique_branch(repo)
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "origin", "wip")
    item = _item(ON.preflight(repo), "remote", "origin/wip")
    assert (item.state, item.action) == ("remote", "keep")
    assert ON.apply(repo) == []
    assert _branch_exists(repo, "wip")  # and the remote-tracking ref is untouched


def test_the_default_branch_is_never_listed(repo):
    _merged_branch(repo)
    assert not [i for i in ON.preflight(repo) if i.name == "main"]


def test_the_api_reports_nothing_as_exit_2_and_the_report_as_text(repo, tmp_path):
    _unique_branch(repo)
    out = api_onboard.preflight(repo)
    assert out.exit == OK
    assert out.data["text"].startswith("keep branch wip")
    assert out.data["leftovers"][0]["state"] == "unique"

    _git(repo, "branch", "-q", "-D", "wip")
    clean = api_onboard.preflight(repo)
    assert clean.exit == NOTHING and "nothing left behind" in clean.reason


def test_the_api_applies_and_says_what_it_removed(repo, tmp_path):
    _merged_branch(repo)
    path = _worktree(repo, tmp_path, "landed")
    out = api_onboard.preflight(repo, apply=True)
    assert out.exit == OK
    assert out.data["removed"] == [f"removed worktree {path} and its branch landed"]
    assert out.data["remaining"] == []
    assert not path.exists()


def _branch_exists(repo: Path, name: str) -> bool:
    r = subprocess.run(
        ["git", "-C", str(repo), "branch", "--list", name], capture_output=True, text=True
    )
    return bool(r.stdout.strip())

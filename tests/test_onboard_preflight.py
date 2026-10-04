"""Onboarding stage 2: every leftover class, and the removal that is safe for each.

The preflight's value is in what it REFUSES to touch: a matching commit message is not
"merged", a worktree holds work when anything beyond a cache is uncommitted OR ignored
(`git worktree remove` deletes ignored files without complaint), a live-or-dead harness
lock is a session's working directory either way, and a stash belongs to whoever made
it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.api import onboard as api_onboard
from ddflow.services import onboard as ON

OK, FAIL, NOTHING = 0, 1, 2


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


def _branch_exists(repo: Path, name: str) -> bool:
    r = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"refs/heads/{name}"],
        capture_output=True,
        text=True,
    )
    return r.returncode == 0


def test_a_clean_repository_has_nothing_to_report(repo):
    assert ON.preflight(repo) == []
    assert "nothing left behind" in ON.render([])


def test_a_merged_branch_is_proposed_and_removed_on_apply(repo):
    _merged_branch(repo)
    item = _item(ON.preflight(repo), "branch", "landed")
    assert (item.state, item.action) == ("merged", "remove")
    assert ON.apply(repo) == [
        {"name": "landed", "kind": "branch", "outcome": "removed", "detail": "branch deleted"}
    ]
    assert not _branch_exists(repo, "landed")


def test_an_unmerged_branch_is_kept_with_its_commits(repo):
    _unique_branch(repo)
    item = _item(ON.preflight(repo), "branch", "wip")
    assert (item.state, item.action) == ("unique", "keep")
    assert "1 commit(s) not in main" in item.detail and "add wip.py" in item.detail
    assert ON.apply(repo) == []
    assert _branch_exists(repo, "wip")


def test_an_approved_name_removes_only_that_one(repo):
    _merged_branch(repo, "one")
    _merged_branch(repo, "two")
    records = ON.apply(repo, ["one"])
    assert [r["name"] for r in records] == ["one"]
    assert not _branch_exists(repo, "one") and _branch_exists(repo, "two")


def test_a_name_not_marked_for_removal_is_refused(repo):
    _unique_branch(repo)
    records = ON.apply(repo, ["wip"])
    assert records == [
        {
            "name": "wip",
            "kind": "branch",
            "outcome": "refused",
            "detail": "not marked for removal [unique]",
        }
    ]
    assert _branch_exists(repo, "wip")


def test_a_merged_clean_worktree_is_removed_and_its_branch_deleted(repo, tmp_path):
    _merged_branch(repo)
    path = _worktree(repo, tmp_path, "landed")
    item = _item(ON.preflight(repo), "worktree", str(path))
    assert (item.state, item.action, item.branch) == ("merged", "remove", "landed")
    assert ON.apply(repo) == [
        {
            "name": str(path),
            "kind": "worktree",
            "outcome": "removed",
            "detail": "worktree and branch landed removed",
        }
    ]
    assert not path.exists() and not _branch_exists(repo, "landed")


def test_caches_are_not_work_but_an_ignored_env_file_is(repo, tmp_path):
    """The prompt says "anything beyond caches": a `__pycache__` or a `.venv` does not
    keep a merged tree, but an ignored `.env`/local file does -- `git worktree remove`
    deletes ignored files silently (rubber_duck on f0d27314)."""
    (repo / ".gitignore").write_text("local.env\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-qm", "ignore local env")
    _merged_branch(repo)
    cache_wt = _worktree(repo, tmp_path, "landed", "cachewt")
    (cache_wt / "__pycache__").mkdir()
    (cache_wt / "__pycache__" / "x.pyc").write_text("cache\n")
    (cache_wt / ".venv").mkdir()
    (cache_wt / ".venv" / "marker").write_text("cache\n")
    assert _item(ON.preflight(repo), "worktree", str(cache_wt)).action == "remove"

    held_wt = _worktree(repo, tmp_path, _merged_branch(repo, "held"), "heldwt")
    (held_wt / "local.env").write_text("SECRET=1\n")
    item = _item(ON.preflight(repo), "worktree", str(held_wt))
    assert (item.state, item.action) == ("dirty", "keep")
    assert "ignored file(s) that are not caches" in item.detail
    records = ON.apply(repo)
    assert [r["name"] for r in records] == [str(cache_wt)], "only the cache-clean tree goes"
    assert not cache_wt.exists()
    assert (held_wt / "local.env").read_text() == "SECRET=1\n"


def test_a_dirty_worktree_holding_work_is_never_removed(repo, tmp_path):
    _merged_branch(repo)
    path = _worktree(repo, tmp_path, "landed")
    (path / "wip.py").write_text("uncommitted\n")
    item = _item(ON.preflight(repo), "worktree", str(path))
    assert (item.state, item.action) == ("dirty", "keep")
    assert "1 uncommitted file(s)" in item.detail
    assert ON.apply(repo) == []
    assert (path / "wip.py").read_text() == "uncommitted\n"


def test_a_dirty_unmerged_worktree_shows_commits_and_files(repo, tmp_path):
    _unique_branch(repo)
    path = _worktree(repo, tmp_path, "wip")
    (path / "wip2.py").write_text("uncommitted\n")
    item = _item(ON.preflight(repo), "worktree", str(path))
    assert item.state == "unique"
    assert "1 commit(s) not in main" in item.detail and "1 uncommitted file(s)" in item.detail


def test_a_stale_lock_is_reported_and_never_removed(repo, tmp_path, monkeypatch):
    """A stale lock is a REPORT: a pid namespace can make a live owner look gone, so
    removing a locked tree stays the operator's call (critic/rubber-duck on f0d27314)."""
    monkeypatch.setattr(ON, "alive", lambda pid: False)
    _merged_branch(repo)
    path = _worktree(repo, tmp_path, "landed")
    _git(repo, "worktree", "lock", "--reason", "agent session (pid 4242)", str(path))
    item = _item(ON.preflight(repo), "worktree", str(path))
    assert (item.state, item.action) == ("locked-stale", "keep")
    assert "process 4242 is gone" in item.detail and "by hand" in item.detail
    assert ON.apply(repo) == []
    assert path.exists() and _branch_exists(repo, "landed")


def test_a_live_lock_is_never_touched(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(ON, "alive", lambda pid: True)
    _merged_branch(repo)
    path = _worktree(repo, tmp_path, "landed")
    _git(repo, "worktree", "lock", "--reason", "agent session (pid 4242)", str(path))
    item = _item(ON.preflight(repo), "worktree", str(path))
    assert (item.state, item.action) == ("locked", "keep")
    assert "process 4242 is alive" in item.detail
    assert ON.apply(repo) == [] and path.exists()


def test_a_lock_without_a_pid_cannot_be_judged(repo, tmp_path):
    _merged_branch(repo)
    path = _worktree(repo, tmp_path, "landed")
    _git(repo, "worktree", "lock", "--reason", "the agent harness", str(path))
    item = _item(ON.preflight(repo), "worktree", str(path))
    assert (item.state, item.action) == ("locked", "keep")
    assert "cannot tell" in item.detail
    assert ON.apply(repo) == [] and path.exists()


def test_a_worktree_git_cannot_inspect_is_not_clean(repo, tmp_path):
    _merged_branch(repo)
    path = _worktree(repo, tmp_path, "landed")
    (path / ".git").write_text("gitdir: /nonexistent\n")
    item = _item(ON.preflight(repo), "worktree", str(path))
    assert (item.state, item.action) == ("unreadable", "keep")
    assert "could not run" in item.detail
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
    r = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "refs/remotes/origin/wip"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, "the remote-tracking ref must be untouched by apply"


def test_the_caller_and_the_main_checkout_are_never_candidates(repo, tmp_path):
    _merged_branch(repo)
    _unique_branch(repo)
    _worktree(repo, tmp_path, "landed", "mainlike")  # a real candidate, unlike the primary
    caller_wt = _worktree(repo, tmp_path, "wip", "callerwt")
    # From a linked worktree, the MAIN checkout is an entry like any other.
    from_wt = ON.preflight(caller_wt)
    assert not [i for i in from_wt if i.name == str(repo) or i.name == str(caller_wt)]
    assert not [i for i in from_wt if i.name == "main"]
    # From a subdirectory of the primary, the primary is no candidate either.
    (repo / "sub").mkdir()
    from_sub = ON.preflight(repo / "sub")
    assert not [i for i in from_sub if i.name == "main"]


def test_the_api_reports_nothing_as_exit_2_and_the_report_as_text(repo):
    _unique_branch(repo)
    out = api_onboard.preflight(repo)
    assert out.exit == OK
    assert out.data["text"].startswith("keep branch wip")
    assert out.data["leftovers"][0]["state"] == "unique"


def test_the_api_applies_only_what_it_is_told_and_reports_records(repo, tmp_path):
    _merged_branch(repo, "one")
    _merged_branch(repo, "two")
    out = api_onboard.preflight(repo, apply=True, only=["one"])
    assert out.exit == OK
    assert [r["name"] for r in out.data["removed"]] == ["one"]
    assert out.data["failed"] == []
    assert not _branch_exists(repo, "one") and _branch_exists(repo, "two")

    _git(repo, "branch", "-q", "-D", "two")
    clean = api_onboard.preflight(repo)
    assert clean.exit == NOTHING and "nothing left behind" in clean.reason

"""ddflow commits its own event shards at complete and release (Bcd3512c891)."""

from __future__ import annotations

import subprocess

from conftest import run_cli

from ddflow.services import eventcommit as EC


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _project(repo):
    assert run_cli(repo, "init")[0] == 0
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "adopt")
    assert run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "a.py")[0] == 0


def test_complete_commits_the_shards_and_nothing_else(repo):
    _project(repo)
    (repo / "staged.txt").write_text("the operator's own work\n")
    _git(repo, "add", "staged.txt")
    assert run_cli(repo, "complete", "T1", "--force")[0] == 0
    assert EC.uncommitted_shards(repo) == []
    assert _git(repo, "log", "-1", "--format=%s").startswith("events: complete T1")
    files = _git(repo, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert files and all(f.startswith(".ddflow/events/") for f in files), files
    assert "A  staged.txt" in _git(repo, "status", "--porcelain")  # still staged, uncommitted


def test_release_commits_the_shards(repo):
    _project(repo)
    assert run_cli(repo, "claim", "T1", "--no-worktree")[0] == 0
    assert run_cli(repo, "release", "T1")[0] == 0
    assert EC.uncommitted_shards(repo) == []
    assert _git(repo, "log", "-1", "--format=%s") == "events: release T1"


def test_a_busy_index_is_reported_never_fails_the_completion(repo):
    from ddflow.api import lifecycle as LC

    _project(repo)
    lock = repo / ".git" / "index.lock"
    lock.write_text("")
    try:
        out = LC.complete(repo, "T1", force=True)
    finally:
        lock.unlink()
    assert out.exit == 0 and "another git command holds the index" in out.data["events_note"]
    assert EC.uncommitted_shards(repo)


def test_off_leaves_the_log_to_the_operator(repo):
    _project(repo)
    assert run_cli(repo, "config", "log.commit_events", "false")[0] == 0
    _git(repo, "commit", "-qam", "config")
    assert run_cli(repo, "complete", "T1", "--force")[0] == 0
    assert EC.uncommitted_shards(repo)


def test_doctor_names_uncommitted_shards(repo):
    _project(repo)
    assert run_cli(repo, "config", "log.commit_events", "false")[0] == 0
    run_cli(repo, "complete", "T1", "--force")
    out = run_cli(repo, "doctor")[1]
    assert "event shard(s) not committed" in out

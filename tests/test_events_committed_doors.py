"""Event shards are also committed at merge and stop; half-written ones are skipped."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from conftest import run_cli
from helpers import git as _git

from ddflow.services import eventcommit as EC


def _project(repo):
    assert run_cli(repo, "init")[0] == 0
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "adopt")
    assert run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "a.py")[0] == 0


def test_stop_commits_the_shards(repo):
    from ddflow.api import knowledge as K

    _project(repo)
    assert EC.uncommitted_shards(repo)
    out = K.session_end(repo, "s1")
    assert "events_committed" in out.data
    assert EC.uncommitted_shards(repo) == []


def test_merge_commits_the_shards(repo):
    import json

    _project(repo)
    (repo / "a.py").write_text("x = 0\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    code, out, err = run_cli(repo, "--json", "claim", "T1")
    assert code == 0, out + err
    wt = Path(json.loads(out)["worktree"])
    (wt / "a.py").write_text("x = 1\n")
    _git(wt, "commit", "-qam", "work")
    code, out, err = run_cli(repo, "merge", "T1")
    assert code == 0, out + err
    assert EC.uncommitted_shards(repo) == [], out
    assert _git(repo, "log", "-1", "--format=%s") == "events: merge T1"


def test_a_door_left_out_commits_nothing(repo):
    _project(repo)
    assert run_cli(repo, "config", "log.commit_events_at", "merge")[0] == 0
    _git(repo, "commit", "-qam", "config")
    assert run_cli(repo, "complete", "T1", "--force")[0] == 0
    assert EC.uncommitted_shards(repo)


def test_a_half_written_shard_is_skipped_not_committed(repo):
    _project(repo)
    half = repo / ".ddflow" / "events" / "live-writer.jsonl"
    half.write_text('{"a": 1}\n{"b": ')
    sha, note = EC.commit_shards(repo, "x")
    assert sha and "live-writer.jsonl" in note
    files = _git(repo, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert files and ".ddflow/events/live-writer.jsonl" not in files
    assert all(f.startswith(".ddflow/events/") for f in files)
    assert EC.uncommitted_shards(repo) == [".ddflow/events/live-writer.jsonl"]


def test_a_message_template_is_used(repo):
    _project(repo)
    assert run_cli(repo, "config", "log.commit_events_message", "ev {reason}")[0] == 0
    _git(repo, "commit", "-qam", "config")
    assert run_cli(repo, "complete", "T1", "--force")[0] == 0
    assert _git(repo, "log", "-1", "--format=%s") == "ev complete T1"


def test_a_failing_hook_does_not_fail_the_operation(repo):
    from ddflow.api import lifecycle as LC

    _project(repo)
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    out = LC.complete(repo, "T1", force=True)
    assert out.exit == 0 and out.data["events_note"]


def _hook(repo, tmp_path, mode):
    """Run the hook with a stand-in `ddflow` that reports the mode, so the hook's reading
    of `[log].pre_push_events` is what is tested."""
    _project(repo)
    (repo / ".ddflow" / "events" / "loose.jsonl").write_text("{}\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "ddflow"
    fake.write_text(f"#!/bin/sh\necho \"log.pre_push_events = '{mode}'   [default]\"\n")
    fake.chmod(0o755)
    stub = bindir / "pre-commit"  # present, so the hook runs to its end
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)
    hook = Path(__file__).resolve().parents[1] / "scripts" / "ci" / "pre-push"
    env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}"}
    return subprocess.run(
        [shutil.which("bash"), str(hook), "origin"], input="", cwd=repo,
        capture_output=True, text=True, check=False, env=env,
    )  # fmt: skip


def test_pre_push_block_mode_refuses(repo, tmp_path):
    p = _hook(repo, tmp_path, "block")
    assert p.returncode == 1 and "refused" in p.stderr, p.stderr


def test_pre_push_off_is_silent(repo, tmp_path):
    p = _hook(repo, tmp_path, "off")
    assert "not committed" not in p.stderr and p.returncode == 0


def test_pre_push_warn_mode_does_not_refuse_for_the_shards(repo, tmp_path):
    p = _hook(repo, tmp_path, "warn")
    assert "not committed" in p.stderr and p.returncode == 0


def test_the_shards_are_read_under_the_log_lock_and_the_commit_is_not(repo, monkeypatch):
    """A ddflow writer cannot append between the whole-line check and the read of the shards;
    the commit, whose hooks can run for minutes, does not hold the lock (the hooks of a
    project must not make every other ddflow command time out)."""
    from ddflow.infra import log as L
    from ddflow.services import eventcommit

    _project(repo)
    held = {}

    def lock_depth(root):
        return L._HELD.get(L._held_key(L.EventLog(root).lock_path), 0)

    real_read = eventcommit._read_whole
    real_git = eventcommit.W.git

    def read(root, rel):
        held["read"] = lock_depth(root)
        return real_read(root, rel)

    def git(root, *args, **kw):
        if args and args[0] == "commit":
            held["commit"] = lock_depth(root)
        return real_git(root, *args, **kw)

    monkeypatch.setattr(eventcommit, "_read_whole", read)
    monkeypatch.setattr(eventcommit.W, "git", git)
    from ddflow.api import knowledge as K

    K.session_end(repo, "s1")
    assert "commit" in held, "the commit never ran, so it proves nothing"
    assert held.get("read", 0) > 0 and held["commit"] == 0, held


def test_a_commit_made_between_the_read_and_the_commit_is_not_reverted(repo, monkeypatch):
    """The private index is built on HEAD as it is when the commit is made: another commit
    that landed after the shards were read keeps its changes in the event-log commit."""
    from ddflow.services import eventcommit

    _project(repo)
    (repo / "other.txt").write_text("v1\n")
    _git(repo, "add", "other.txt")
    _git(repo, "commit", "-qm", "other v1", "--no-verify")
    shard = next((repo / ".ddflow" / "events").glob("*.jsonl"))
    with shard.open("a") as f:
        f.write(shard.read_text().splitlines()[-1] + "\n")
    real = eventcommit._private_index

    def after_someone_commits(root, taken, index):
        (root / "other.txt").write_text("v2\n")
        _git(root, "add", "other.txt")
        _git(root, "commit", "-qm", "other v2", "--no-verify")
        return real(root, taken, index)  # builds on HEAD as it is now

    monkeypatch.setattr(eventcommit, "_private_index", after_someone_commits)
    sha, note = eventcommit.commit_shards(repo, "mine")
    assert sha and "HEAD moved" not in note, (sha, note)
    assert _git(repo, "show", "HEAD:other.txt") == "v2", "the interleaved commit was reverted"
    assert _git(repo, "diff", "--cached", "--name-only") == ""


def test_merge_commits_what_the_after_merge_check_recorded(repo, tmp_path, monkeypatch):
    """The merge-door commit comes after `check_after_merge`, so its ci.result is in it."""
    import json

    bindir = tmp_path / "bin"
    bindir.mkdir()
    pc = bindir / "pre-commit"
    pc.write_text("#!/bin/sh\necho 'ruff check.....................Passed'\n")
    pc.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    _project(repo)
    (repo / ".pre-commit-config.yaml").write_text("repos: []\n")
    (repo / "a.py").write_text("x = 0\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    code, out, err = run_cli(repo, "--json", "claim", "T1")
    wt = Path(json.loads(out)["worktree"])
    (wt / "a.py").write_text("x = 1\n")
    _git(wt, "commit", "-qam", "work")
    code, out, err = run_cli(repo, "merge", "T1")
    assert code == 0, out + err
    assert _git(repo, "log", "-1", "--format=%s") == "events: merge T1"
    assert "ci.result" in _git(repo, "show", "HEAD")
    assert EC.uncommitted_shards(repo) == []


def test_a_commit_that_lands_just_before_ours_begins_is_kept(repo, monkeypatch):
    """The one span git's own ref check cannot cover (between the index being built and git
    beginning the commit): the commit that landed there would be undone, so the event-log
    commit is remade on top of it, and the note says so."""
    from ddflow.services import eventcommit

    _project(repo)
    (repo / "other.txt").write_text("v1\n")
    _git(repo, "add", "other.txt")
    _git(repo, "commit", "-qm", "other v1", "--no-verify")
    shard = next((repo / ".ddflow" / "events").glob("*.jsonl"))
    with shard.open("a") as f:
        f.write(shard.read_text().splitlines()[-1] + "\n")
    real = eventcommit.W.git

    def foreign_commit_first(root, *args, **kw):
        if args and args[0] == "commit":
            (root / "other.txt").write_text("v2\n")
            _git(root, "add", "other.txt")
            _git(root, "commit", "-qm", "other v2", "--no-verify")
        return real(root, *args, **kw)

    monkeypatch.setattr(eventcommit.W, "git", foreign_commit_first)
    sha, note = eventcommit.commit_shards(repo, "mine")
    assert sha and "made again" in note, (sha, note)
    assert _git(repo, "show", "HEAD:other.txt") == "v2", "the commit that landed was undone"
    assert _git(repo, "log", "-1", "--format=%s").startswith("events:")
    assert _git(repo, "log", "-2", "--format=%s").splitlines()[1] == "other v2"
    assert eventcommit.uncommitted_shards(repo) == []
    assert _git(repo, "diff", "--cached", "--name-only") == ""

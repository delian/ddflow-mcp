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

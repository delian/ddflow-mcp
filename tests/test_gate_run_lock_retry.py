"""B-gate-run-dies-on-events-lock: a `gate run` whose start needs the events.lock must not
die with a raw `TimeoutError` when another lane holds it past one acquire window. It
retries, visibly, for a bounded time, and gives up cleanly (exit 2) instead of tracebacking
before its command starts."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import run_cli
from helpers import git as _git

from ddflow.api.gates import _run

HOLDER = """
import sys, time
from pathlib import Path
from ddflow.infra.log import _flock
with _flock(Path(sys.argv[1]), 5):
    print("held", flush=True)
    time.sleep(30)
"""


def _hold(lock: Path) -> subprocess.Popen:
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    p = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(lock)], stdout=subprocess.PIPE, text=True, env=env
    )
    assert p.stdout.readline().strip() == "held"
    return p


def test_gate_run_retries_a_held_events_lock_and_stops_cleanly(
    repo, tmp_path, monkeypatch, capsys
):
    run_cli(repo, "init")
    run_cli(repo, "config", "--set", "gate.ci.command", "echo ok")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    tree = repo.parent / "agent-tree"
    _git(repo, "worktree", "add", "-q", str(tree), "-b", "agent-work")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "a.py")
    assert run_cli(tree, "claim", "T1", "--no-worktree")[0] == 0
    # A short acquire window plus a short retry bound keep the test fast; the holder keeps
    # the lock for the whole of it, so the run must give up cleanly, never raise.
    run_cli(repo, "config", "--set", "lease.acquire_timeout_s", "1")
    monkeypatch.setattr(_run, "_GATE_START_LOCK_RETRY_S", 3.0)

    p = _hold(repo / ".ddflow" / "events.lock")
    try:
        result = _run.run(repo, "T1", "ci", agent="a1")
    finally:
        p.kill()
        p.wait()

    assert result.exit == 2, result.reason  # NOTHING: it could not record its start
    assert "could not record its start" in result.reason
    assert result.data["outcome"] == ""
    # Retried visibly before giving up: the note names the lock and that it is retrying.
    assert "waiting to record the run start" in capsys.readouterr().err

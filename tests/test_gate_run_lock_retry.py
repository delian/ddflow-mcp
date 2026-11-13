"""B-gate-run-dies-on-events-lock: a `gate run` whose start needs the events.lock must not
die with a raw `TimeoutError` when another lane holds it past one acquire window. It
retries, visibly, for a bounded time, and gives up cleanly (exit 2) instead of tracebacking
before its command starts."""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import run_cli
from helpers import git as _git

from ddflow.api.gates import _run
from ddflow.infra.log import _flock

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


def test_gate_run_retries_a_held_events_lock_and_stops_cleanly(repo, tmp_path, monkeypatch, capsys):
    ran = tmp_path / "ran"
    run_cli(repo, "init")
    run_cli(repo, "config", "--set", "gate.ci.command", f"touch {shlex.quote(str(ran))}")
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
        t0 = time.monotonic()
        result = _run.run(repo, "T1", "ci", agent="a1")
        elapsed = time.monotonic() - t0
    finally:
        p.kill()
        p.wait()

    assert result.exit == 2, result.reason  # NOTHING: it could not record its start
    assert "could not record its start" in result.reason
    assert result.data["outcome"] == ""
    assert not ran.exists()  # it gave up BEFORE its command ever started
    # It retried for most of the bound (one timed-out acquire, then a wait), not a single
    # acquire that gave up at once, and not far longer than the bound either.
    assert 2.0 <= elapsed < 10.0
    # Retried visibly before giving up: the note names the lock and that it is retrying.
    assert "waiting to record the run start" in capsys.readouterr().err


def test_a_running_gate_does_not_hold_the_events_lock(repo, tmp_path):
    # The second half of B-gate-run-dies-on-events-lock: a long gate run must record its
    # start (briefly) and then NOT hold events.lock while its command runs, or every other
    # lane's heartbeat blocks behind this run's commit. While the command sleeps, another
    # writer must acquire the lock at once.
    began = tmp_path / "began"
    run_cli(repo, "init")
    run_cli(
        repo,
        "config",
        "--set",
        "gate.ci.command",
        f"touch {shlex.quote(str(began))}; sleep 30",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    tree = repo.parent / "agent-tree"
    _git(repo, "worktree", "add", "-q", str(tree), "-b", "agent-work")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "a.py")
    assert run_cli(tree, "claim", "T1", "--no-worktree")[0] == 0

    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    p = subprocess.Popen(
        [sys.executable, "-m", "ddflow", "--repo", str(repo), "gate", "run", "T1", "ci"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 30
        while not began.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert began.exists(), "the gate command never started"
        assert p.poll() is None  # the gate is still running while we take the lock
        # Taken while the command runs: a held lock would wait past 5s and raise TimeoutError.
        with _flock(repo / ".ddflow" / "events.lock", 5):
            pass
    finally:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.wait(timeout=30)

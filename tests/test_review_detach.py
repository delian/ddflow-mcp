"""`ddflow review --detach` runs the review in its OWN session and streams to a durable log,
so the harness's time limit kills only the launcher and the review outlives it (bug
Be927e56fb6): a background `ddflow review --delta` died at the 30 min limit with output only
"[killed]" and no recorded verdict. The launcher returns at once; the review's progress goes
to a log that survives a kill, so a cut run is never silent."""

from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git as _git

pytestmark = pytest.mark.skipif(os.name != "posix", reason="setsid is POSIX")


def _setup(repo: Path, tmp_path: Path) -> tuple[Path, Path]:
    """A reviewer that sleeps while `slow` exists, else answers at once; branch `feat` with a
    change. Returns (the file the reviewer touches when it starts, the reviewer's pidfile)."""
    began, pidfile = tmp_path / "began", tmp_path / "reviewer.pid"
    cli = tmp_path / "reviewer"
    cli.write_text(
        f"#!/bin/sh\ncat >/dev/null\nif [ -e '{tmp_path / 'slow'}' ]; then echo $$ > '{pidfile}'; touch '{began}'; exec sleep 120; fi\n"
        "echo 'STATUS: NO FINDINGS'\n"
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n[review]\nmax_rounds = 0\n"
        f'[[reviewer]]\nname = "r"\nkind = "command"\ncommand = "{cli}"\n'
        'model = "gemini-2.5-pro"\ngates = ["critic"]\n'
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "*.py")
    _git(repo, "checkout", "-q", "-b", "feat")
    (repo / "x.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "work")
    return began, pidfile


def _detach(repo: Path) -> tuple[str, str]:
    """Run `ddflow review --detach` and wait for the LAUNCHER; (stdout, stderr)."""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    p = subprocess.run(
        [sys.executable, "-m", "ddflow", "--repo", str(repo), "review", "T1", "--gate",
         "critic", "--branch", "feat", "--detach"],
        capture_output=True, text=True, env=env, timeout=120,
    )  # fmt: skip
    assert p.returncode == 0, f"exit {p.returncode}: {p.stdout!r} {p.stderr!r}"
    return p.stdout, p.stderr


def _pid(out: str) -> int:
    return int(re.search(r"pid (\d+)", out).group(1))


def _log(out: str) -> Path:
    return Path(re.search(r"streams to (\S+)", out).group(1))


def _gate(repo: Path) -> dict:
    _code, shown, _err = run_cli(repo, "--json", "show", "T1")
    return json.loads(shown).get("gates", {}).get("critic", {})


def _kill_reviewer(pidfile: Path, detached_pid: int) -> None:
    with contextlib.suppress(ProcessLookupError, FileNotFoundError):
        os.kill(int(pidfile.read_text()), signal.SIGKILL)
    with contextlib.suppress(ProcessLookupError):
        os.killpg(detached_pid, signal.SIGKILL)


def test_detach_returns_at_once_while_the_review_keeps_running(repo, tmp_path):
    began, pidfile = _setup(repo, tmp_path)
    (tmp_path / "slow").touch()
    out, _err = _detach(repo)
    log = _log(out)
    # The reviewer's start marker appears after the launcher already returned: the review
    # runs in its own session, so the command that started it held only the launcher.
    deadline = time.monotonic() + 60
    while not began.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert began.exists(), "the detached review never started: " + log.read_text()
    _kill_reviewer(pidfile, _pid(out))


def test_a_killed_detached_review_leaves_its_streamed_output(repo, tmp_path):
    began, pidfile = _setup(repo, tmp_path)
    (tmp_path / "slow").touch()
    out, _err = _detach(repo)
    pid, log = _pid(out), _log(out)
    deadline = time.monotonic() + 60
    while not began.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert began.exists(), "the reviewer never started: " + log.read_text()
    body = log.read_text()
    # Streamed BEFORE it was cut down, so a killed review is not a silent "[killed]".
    assert "→ r (" in body and "reviewing" in body, body
    _kill_reviewer(pidfile, pid)
    assert "reviewing" in log.read_text(), log.read_text()


def test_a_detached_review_records_passed_without_the_launcher(repo, tmp_path):
    _setup(repo, tmp_path)
    out, _err = _detach(repo)
    log = _log(out)
    deadline = time.monotonic() + 60
    gate = {}
    while time.monotonic() < deadline and gate.get("outcome") != "passed":
        gate = _gate(repo)
        time.sleep(0.2)
    assert gate.get("outcome") == "passed", (gate, log.read_text() if log.exists() else "")

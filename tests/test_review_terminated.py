"""A review killed by `timeout` (SIGTERM) says so and records the gate unavailable (bug
Bed9d75dd1c): it used to vanish -- output ended mid-wait, no error, nothing on the record."""

from __future__ import annotations

import contextlib
import json
import os
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

pytestmark = pytest.mark.skipif(os.name != "posix", reason="signals are POSIX")


def _setup(repo: Path, tmp_path: Path) -> Path:
    began = tmp_path / "began"
    cli = tmp_path / "slow-reviewer"
    cli.write_text(f"#!/bin/sh\ncat >/dev/null\ntouch '{began}'\nsleep 120\n")
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n"
        f'[[reviewer]]\nname = "slow"\nkind = "command"\ncommand = "{cli}"\n'
        'model = "gemini-2.5-pro"\ngates = ["critic"]\n'
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "*.py")
    _git(repo, "checkout", "-q", "-b", "feat")
    (repo / "x.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "work")
    return began


def test_a_sigtermed_review_says_so_and_records_the_gate_unavailable(repo, tmp_path):
    began = _setup(repo, tmp_path)
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    p = subprocess.Popen(
        [sys.executable, "-m", "ddflow", "--repo", str(repo), "review", "T1", "--gate", "critic",
         "--full", "--branch", "feat"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
        start_new_session=True, cwd=str(repo),
    )  # fmt: skip
    deadline = time.monotonic() + 120
    while not began.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    if not began.exists():
        p.kill()
        raise AssertionError("the reviewer never started: " + "".join(p.communicate()))
    p.send_signal(signal.SIGTERM)
    p.wait(timeout=60)  # the review ends on its own; the reviewer it started may linger
    with contextlib.suppress(ProcessLookupError):
        os.killpg(p.pid, signal.SIGKILL)
    out, err = p.communicate(timeout=30)
    text = out + err
    assert "terminated by SIGTERM" in text and "not a pass" in text, text
    _code, shown, _e = run_cli(repo, "--json", "show", "T1")
    gate = json.loads(shown)["gates"]["critic"]
    assert gate["outcome"] == "unavailable" and "SIGTERM" in gate["reason"], gate

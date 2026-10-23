"""A review killed by `timeout` (SIGTERM) says so and records the gate unavailable (bug
Bed9d75dd1c): it used to vanish -- output ended mid-wait, no error, nothing on the record --
and its exit waited out the slowest reviewer. An earlier finished round stays the record."""

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


def _setup(repo: Path, tmp_path: Path) -> tuple[Path, Path]:
    """A reviewer that answers at once, or sleeps while `slow` exists; branch `feat` with a
    change. Returns (the file the slow reviewer touches when it starts, the `slow` switch)."""
    began, slow = tmp_path / "began", tmp_path / "slow"
    pidfile = tmp_path / "reviewer.pid"
    cli = tmp_path / "reviewer"
    cli.write_text(
        f"#!/bin/sh\ncat >/dev/null\nif [ -e '{slow}' ]; then echo $$ > '{pidfile}'; touch '{began}'; exec sleep 120; fi\n"
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
    return began, slow


def _review_and_term(repo: Path, began: Path, tmp_path: Path, *, group: bool) -> str:
    """Start `ddflow review`, SIGTERM it once its reviewer is running; its output. The output
    goes to a file, not a pipe: nothing to drain, and no EOF held open by a lingering reviewer."""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    log = tmp_path / "review.out"
    with log.open("w") as sink:
        p = subprocess.Popen(
            [sys.executable, "-m", "ddflow", "--repo", str(repo), "review", "T1", "--gate",
             "critic", "--full", "--branch", "feat"],
            stdout=sink, stderr=subprocess.STDOUT, text=True, env=env,
            start_new_session=True, cwd=str(repo),
        )  # fmt: skip
        try:
            deadline = time.monotonic() + 120
            while not began.exists() and time.monotonic() < deadline and p.poll() is None:
                time.sleep(0.1)
            assert began.exists(), "the reviewer never started: " + log.read_text()
            # `timeout` signals the whole group; a direct kill only the review itself
            (os.killpg if group else os.kill)(p.pid, signal.SIGTERM)
            assert p.wait(timeout=60) != 0, "a killed review must not exit 0"  # ...by itself
            _assert_gone(began.parent / "reviewer.pid")  # ...with its reviewer cancelled
        except BaseException:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(p.pid, signal.SIGKILL)  # the group still exists: p is not reaped
            raise
    return log.read_text()


def _assert_gone(pidfile: Path) -> None:
    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    os.kill(pid, signal.SIGKILL)
    raise AssertionError(f"the reviewer (pid {pid}) outlived the terminated review")


def _gate(repo: Path) -> dict:
    _code, shown, _err = run_cli(repo, "--json", "show", "T1")
    return json.loads(shown)["gates"]["critic"]


def test_a_sigtermed_review_says_so_and_records_the_gate_unavailable(repo, tmp_path):
    began, slow = _setup(repo, tmp_path)
    slow.touch()
    text = _review_and_term(repo, began, tmp_path, group=True)
    assert "terminated by SIGTERM" in text and "not a pass" in text, text
    gate = _gate(repo)
    assert gate["outcome"] == "unavailable" and "SIGTERM" in gate["reason"], gate


def test_a_sigtermed_review_leaves_an_earlier_finished_round_the_record(repo, tmp_path):
    began, slow = _setup(repo, tmp_path)
    assert run_cli(repo, "review", "T1", "--gate", "critic", "--branch", "feat")[0] == 0
    assert _gate(repo)["outcome"] == "passed"
    slow.touch()
    text = _review_and_term(repo, began, tmp_path, group=False)
    assert "terminated by SIGTERM" in text, text
    gate = _gate(repo)
    assert gate["outcome"] == "passed", gate
    assert gate["evidence"]["unavailable_deltas"][0]["reason"].startswith("the review was"), gate

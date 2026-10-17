"""A started gate run always ends in a recorded outcome (B58842554a7).

`gate run unit_tests` moved to the background by a harness vanished with no output and no
record; `gate status` still showed the gate unrecorded. A SIGKILL cannot be caught, so the
run leaves a started-marker that the next gate call sweeps into `unavailable`; a SIGTERM
(the usual timeout) is caught and recorded by the dying run itself.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git as _git

OK = 0
pytestmark = pytest.mark.skipif(os.name != "posix", reason="signals are POSIX")


def _setup(repo: Path, tmp_path: Path) -> tuple[Path, Path]:
    began = tmp_path / "began"
    cmd = (
        f"{sys.executable} -c \"import pathlib,time; pathlib.Path({str(began)!r}).write_text('x'); "
        'time.sleep(120)"'
    )
    run_cli(repo, "init")
    assert run_cli(repo, "config", "--set", "gate.unit_tests.command", cmd)[0] == OK
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    tree = repo.parent / "agent-tree"
    _git(repo, "worktree", "add", "-q", str(tree), "-b", "agent-work")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "a.py")
    assert run_cli(tree, "claim", "T1", "--no-worktree")[0] == OK
    return tree, began


def _start(repo: Path, began: Path) -> subprocess.Popen:
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    p = subprocess.Popen(
        [sys.executable, "-m", "ddflow", "--repo", str(repo), "gate", "run", "T1", "unit_tests"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=True,
    )
    deadline = time.monotonic() + 120
    while not began.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert began.exists(), "the gate command never started"
    return p


def _gate(repo: Path) -> dict:
    _code, out, _err = run_cli(repo, "--json", "show", "T1")
    return json.loads(out)["gates"].get("unit_tests", {})


def test_a_sigkilled_run_is_recorded_unavailable_by_the_next_call(repo, tmp_path):
    repo, began = _setup(repo, tmp_path)
    p = _start(repo, began)
    os.killpg(p.pid, signal.SIGKILL)  # the process and the command it was running
    p.communicate(timeout=30)
    code, out, err = run_cli(repo, "gate", "status", "T1")
    assert code == OK, out + err
    gate = _gate(repo)
    assert gate.get("outcome") == "unavailable", gate
    assert "killed" in gate["reason"], gate


def test_a_sigterm_run_records_unavailable_before_it_exits(repo, tmp_path):
    repo, began = _setup(repo, tmp_path)
    p = _start(repo, began)
    p.send_signal(signal.SIGTERM)
    p.communicate(timeout=60)
    gate = _gate(repo)
    assert gate.get("outcome") == "unavailable", gate
    assert "SIGTERM" in gate["reason"], gate
    assert not list(tmp_path.rglob("*.running.*.json"))


def test_a_dead_marker_for_a_gate_that_is_gone_is_dropped_without_a_record(repo, tmp_path):
    tree, _began = _setup(repo, tmp_path)
    d = next(tmp_path.rglob(".ddflow")) / "runs" / "T1"
    d.mkdir(parents=True, exist_ok=True)
    stale = d / "no_such_gate.running.1.json"
    stale.write_text(
        json.dumps(
            {
                "item": "T1",
                "gate": "no_such_gate",
                "pid": 2**22,
                "pid_start": "",
                "host": socket.gethostname(),
                "at": "2026-01-01T00:00:00.000000Z",
                "path": "",
            }
        )
    )
    assert run_cli(tree, "gate", "status", "T1")[0] == OK
    assert not stale.exists()

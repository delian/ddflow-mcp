"""A second `gate run` of the same gate on the SAME commit is refused while the first is alive
(DX-gate-run-same-commit-live; left over from B58842554a7). A run on a newer commit stays
allowed. Two runs would race for one outcome and, for `ci`, queue behind one another."""

from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from test_gate_run_killed import _setup, _start

from ddflow.services.gates import running as RUN

pytestmark = pytest.mark.skipif(os.name != "posix", reason="signals are POSIX")


def _reap(p) -> None:
    os.killpg(p.pid, signal.SIGKILL)
    p.wait(timeout=30)


def test_a_second_run_on_the_same_commit_is_refused_naming_the_first(repo, tmp_path):
    repo, began = _setup(repo, tmp_path)  # the agent's tree, as in test_gate_run_killed
    first = _start(repo, began)
    try:
        code, out, err = run_cli(repo, "gate", "run", "T1", "unit_tests")
        text = out + err
        assert code == 2, text
        assert "already running" in text and f"pid {first.pid}" in text, text
    finally:
        _reap(first)


def test_live_run_judges_commit_liveness_and_host(tmp_path):
    repo = tmp_path
    m = RUN.mark_started(repo, "T1", "ci", "tree-aaa")
    assert m is not None and m.sha == "tree-aaa"
    other_pid_marker = Path(m.path)
    # this process is the run itself: not its own twin
    assert RUN.live_run(repo, "T1", "ci", "tree-aaa") is None
    # a live OTHER process on the same commit is a twin; a newer commit or another gate is not
    import json

    data = json.loads(other_pid_marker.read_text())
    data["pid"] = os.getppid()
    data["pid_start"] = RUN.proc_start(os.getppid())
    other_pid_marker.write_text(json.dumps(data))
    assert RUN.live_run(repo, "T1", "ci", "tree-aaa").pid == os.getppid()
    assert RUN.live_run(repo, "T1", "ci", "tree-bbb") is None
    assert RUN.live_run(repo, "T1", "unit_tests", "tree-aaa") is None
    assert RUN.live_run(repo, "T1", "ci", "") is None
    data["host"] = "some-other-host.invalid"  # not judged from here
    other_pid_marker.write_text(json.dumps(data))
    assert RUN.live_run(repo, "T1", "ci", "tree-aaa") is None
    data["host"] = RUN.H.hostname()
    data["pid"] = 2**22 + 7  # gone
    other_pid_marker.write_text(json.dumps(data))
    assert RUN.live_run(repo, "T1", "ci", "tree-aaa") is None

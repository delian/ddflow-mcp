"""A reader that closes the pipe early is normal shell use: `ddflow ... | head -1`
exits quietly (B9834dabc40).

It used to end with a BrokenPipeError traceback (and "Exception ignored on flushing
sys.stdout") on stderr.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.infra.log import EventLog

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "argv",
    [
        ["list", "task", "--all", "--limit", "1000"],
        ["--json", "list", "task", "--all", "--limit", "1000"],
        ["board"],
    ],
)
def test_a_closed_pipe_ends_quietly(repo, argv):
    run_cli(repo, "init")
    log = EventLog(repo, "seed")
    for i in range(600):
        log.append(
            "task.added", f"T{i}", {"title": f"a rather long title for task number {i} " * 3}
        )
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    cmd = [sys.executable, "-m", "ddflow", "--repo", str(repo), *argv]
    ddflow = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    assert ddflow.stdout is not None and ddflow.stderr is not None
    ddflow.stdout.read(10)
    ddflow.stdout.close()  # the reader (`head -c 10`) is gone
    err = ddflow.stderr.read().decode()
    ddflow.wait(timeout=60)
    assert "Traceback" not in err and "BrokenPipeError" not in err, err
    assert ddflow.returncode in (0, 141), ddflow.returncode

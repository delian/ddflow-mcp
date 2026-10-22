"""A shell line `run_shell` started dies with its owner (Ba3b7509530): an orphaned ci suite ran
for 8.3 hours after the gate run that started it was gone."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="process groups")

OWNER = "import sys\nfrom ddflow.infra import proc\nproc.run_shell(sys.argv[1], timeout_s=600)\n"


def _gone(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except OSError:
        return True
    return state in ("Z", "X")  # a zombie nobody reaped is dead


def _until(pred, timeout=15.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


def test_a_killed_owner_takes_its_whole_process_group_with_it(tmp_path: Path):
    pids = tmp_path / "pids"
    line = f"(echo $$ >> {pids}; exec sleep 600) & echo $$ >> {pids}; wait"
    owner = subprocess.Popen([sys.executable, "-c", OWNER, line])
    try:
        assert _until(lambda: pids.exists() and len(pids.read_text().split()) == 2)
        running = [int(p) for p in pids.read_text().split()]
        assert not any(_gone(p) for p in running)
        time.sleep(1.0)  # the watcher has started
        os.kill(owner.pid, signal.SIGKILL)  # the owner dies without cleaning up
        owner.wait()
        assert _until(lambda: all(_gone(p) for p in running)), "the suite outlived its owner"
    finally:
        for p in locals().get("running", []):
            try:
                os.kill(p, signal.SIGKILL)
            except OSError:
                pass
        owner.kill()


def test_a_finished_run_leaves_no_watcher_behind(tmp_path: Path):
    from ddflow.infra import proc

    before = _watchers()
    proc.run_shell("true", timeout_s=30)
    assert _until(lambda: _watchers() <= before, timeout=5)


def _watchers() -> int:
    n = 0
    for d in Path("/proc").iterdir():
        if d.name.isdigit():
            try:
                if b"ddflow-owner-watch" in (d / "cmdline").read_bytes():
                    n += 1
            except OSError:
                pass
    return n

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

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")

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
    line = f"sleep 600 & echo $! >> {pids}; echo $$ >> {pids}; wait"
    owner = subprocess.Popen([sys.executable, "-c", OWNER, line])
    try:
        assert _until(lambda: pids.exists() and len(pids.read_text().split()) == 2)
        running = [int(p) for p in pids.read_text().split()]
        assert not any(_gone(p) for p in running)
        assert _until(lambda: _watching(running[1])), "no watcher started"
        os.kill(owner.pid, signal.SIGKILL)  # the owner dies without cleaning up
        owner.wait()
        assert _until(lambda: all(_gone(p) for p in running)), "the suite outlived its owner"
    finally:
        for word in pids.read_text().split() if pids.exists() else []:
            try:
                os.kill(int(word), signal.SIGKILL)
            except OSError:
                pass
        owner.kill()


def test_a_finished_run_leaves_no_watcher_behind(tmp_path: Path):
    from ddflow.infra import proc

    pid_file = tmp_path / "pid"
    proc.run_shell(f"echo $$ > {pid_file}", timeout_s=30)
    shell = int(pid_file.read_text())
    assert _until(lambda: not _watching(shell), timeout=10)  # ended
    assert _until(lambda: not _zombie_children(), timeout=10)  # and reaped


def _watching(group: int) -> bool:
    """A live watcher process for the process group led by ``group``."""
    for d in Path("/proc").iterdir():
        if d.name.isdigit():
            try:
                argv = (d / "cmdline").read_bytes().split(b"\0")
            except OSError:
                continue
            if b"ddflow-owner-watch" in argv and str(group).encode() in argv[2:4]:
                return True
    return False


def _zombie_children() -> list[int]:
    """Children of this process that ended and were never waited for."""
    me = str(os.getpid())
    out = []
    for d in Path("/proc").iterdir():
        if d.name.isdigit():
            try:
                fields = (d / "stat").read_text().rsplit(")", 1)[1].split()
            except OSError:
                continue
            if fields[0] == "Z" and fields[1] == me:
                out.append(int(d.name))
    return out

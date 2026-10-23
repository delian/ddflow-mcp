"""A shell line `run_shell` started dies with its owner (Ba3b7509530): an orphaned ci suite ran
for 8.3 hours after the gate run that started it was gone."""

from __future__ import annotations

import os
import shlex
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
    q = shlex.quote(str(pids))
    line = f"sleep 600 & echo $! >> {q}; echo $$ >> {q}; wait"
    owner = subprocess.Popen([sys.executable, "-c", OWNER, line])
    try:
        assert _until(lambda: pids.exists() and len(pids.read_text().split()) == 2)
        running = [int(p) for p in pids.read_text().split()]
        assert not any(_gone(p) for p in running)
        assert _until(lambda: _armed(owner.pid)), "no armed watcher"
        os.kill(owner.pid, signal.SIGKILL)  # the owner dies without cleaning up
        owner.wait()
        assert _until(lambda: all(_gone(p) for p in running)), "the suite outlived its owner"
    finally:
        for word in pids.read_text().split() if pids.exists() else []:
            try:
                os.kill(int(word), signal.SIGKILL)
            except OSError:
                pass
        if owner.poll() is None:
            owner.kill()
            owner.wait()


def test_a_finished_run_leaves_no_watcher_behind(tmp_path: Path):
    from ddflow.infra import proc

    proc.run_shell("true", timeout_s=30)
    assert _until(lambda: not _watching(os.getpid()), timeout=10)  # ended
    assert _until(lambda: not _zombie_children(), timeout=10)  # and reaped


def _watchers_of(owner_pid: int) -> list[int]:
    """The live watcher processes guarding commands of the process ``owner_pid``."""
    found = []
    for d in Path("/proc").iterdir():
        if d.name.isdigit():
            try:
                argv = (d / "cmdline").read_bytes().split(b"\0")
            except OSError:
                continue
            if b"ddflow-owner-watch" in argv and str(owner_pid).encode() in argv[3:4]:
                found.append(int(d.name))
    return found


def _watching(owner_pid: int) -> bool:
    return bool(_watchers_of(owner_pid))


def _armed(owner_pid: int) -> bool:
    """A watcher that has been told its group: it is past its first read of the pipe, i.e. no
    longer waiting in the kernel on a pipe (/proc/<pid>/wchan names what a sleeper waits in)."""
    for pid in _watchers_of(owner_pid):
        try:
            wchan = Path(f"/proc/{pid}/wchan").read_text().strip()
        except OSError:
            continue
        if wchan not in ("", "0") and "pipe" not in wchan:
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

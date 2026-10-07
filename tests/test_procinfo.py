"""Bug B7395b84149: process liveness.

`jobs.proc_start` and `jobs.alive` read `/proc/<pid>/stat` as UTF-8 text, so a process
whose name (`comm`) is not valid UTF-8 raised UnicodeDecodeError -- not an OSError, so
nothing caught it. And a registered wait checked only that its pid was alive: once the
waiting process died and the kernel handed its pid to another, the dead wait stayed live.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ddflow.services import jobs as J
from ddflow.services import waits as WT

linux = pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="reads /proc")

#: Renames itself to bytes that are not UTF-8, says so, then waits to be killed.
_BAD_NAME = (
    "import sys, time\n"
    "open('/proc/self/comm', 'wb').write(b'ddf\\xff\\xfeow')\n"
    "print('ready', flush=True)\n"
    "time.sleep(60)\n"
)


@pytest.fixture
def badly_named():
    p = subprocess.Popen([sys.executable, "-c", _BAD_NAME], stdout=subprocess.PIPE)
    assert p.stdout is not None and p.stdout.readline().strip() == b"ready"
    assert b"\xff" in Path(f"/proc/{p.pid}/stat").read_bytes()
    try:
        yield p.pid
    finally:
        p.kill()
        p.wait()


@linux
def test_a_process_whose_name_is_not_utf8_has_a_start_time(badly_named) -> None:
    assert J.proc_start(badly_named).isdigit()


@linux
def test_a_process_whose_name_is_not_utf8_is_alive(badly_named) -> None:
    assert J.alive(badly_named) is True


@linux
def test_a_wait_whose_pid_was_reused_is_not_live(tmp_path: Path) -> None:
    """The registration names this test's own pid, but a start time that pid never had:
    the process that registered is gone and the pid now belongs to another."""
    w = WT.register(tmp_path, WT.Waiter(agent="a1", item="T1", until=time.time() + 60))
    assert [x.agent for x in WT.live_waiters(tmp_path)] == ["a1"]
    raw = json.loads(Path(w.path).read_text("utf-8"))
    assert raw["pid"] == os.getpid() and raw["pid_start"] == J.proc_start(os.getpid())
    raw["pid_start"] = "1"  # the start time of the process that really registered
    Path(w.path).write_text(json.dumps(raw), "utf-8")
    assert WT.live_waiters(tmp_path) == []


@linux
def test_an_older_registration_without_a_start_time_still_counts(tmp_path: Path) -> None:
    w = WT.register(tmp_path, WT.Waiter(agent="a1", item="T1", until=time.time() + 60))
    raw = json.loads(Path(w.path).read_text("utf-8"))
    raw.pop("pid_start", None)
    Path(w.path).write_text(json.dumps(raw), "utf-8")
    assert [x.agent for x in WT.live_waiters(tmp_path)] == ["a1"]

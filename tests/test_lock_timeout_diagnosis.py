"""B184: a lock timeout names the waiter, the wait and (on Linux) the holder, so overload is
told from a wedged agent. The lock file itself is never written."""

import subprocess
import sys
import time
from pathlib import Path

import pytest

from ddflow.infra import log as L
from ddflow.infra.log import _flock

HOLDER = """
import sys, time
from pathlib import Path
from ddflow.infra.log import _flock
with _flock(Path(sys.argv[1]), 5):
    print("held", flush=True)
    time.sleep(30)
"""

needs_proc_locks = pytest.mark.skipif(
    not Path("/proc/locks").exists(), reason="the holder comes from /proc/locks (Linux)"
)


def _hold(lock: Path) -> subprocess.Popen:
    p = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(lock)], stdout=subprocess.PIPE, text=True
    )
    assert p.stdout.readline().strip() == "held"
    return p


def test_timeout_reports_the_wait_and_this_process(tmp_path):
    lock = tmp_path / "events.lock"
    p = _hold(lock)
    try:
        t0 = time.monotonic()
        with pytest.raises(TimeoutError) as e, _flock(lock, 0.3):
            pass
        msg = str(e.value)
        assert time.monotonic() - t0 < 5
        assert "waited 0." in msg or "waited 1." in msg, msg
        assert "this is pid" in msg and "overloaded" in msg, msg
    finally:
        p.kill()
        p.wait()


@needs_proc_locks
def test_timeout_names_the_holder_and_its_command(tmp_path):
    lock = tmp_path / "events.lock"
    p = _hold(lock)
    try:
        with pytest.raises(TimeoutError) as e, _flock(lock, 0.3):
            pass
        msg = str(e.value)
        assert f"Held by pid {p.pid} (" in msg and "ddflow.infra.log" in msg, msg
    finally:
        p.kill()
        p.wait()


@needs_proc_locks
def test_no_holder_is_named_once_the_lock_is_free(tmp_path):
    lock = tmp_path / "events.lock"
    with _flock(lock, 1):
        pass
    assert L._holder_note(lock) == ""


def test_the_lock_file_is_never_written(tmp_path):
    """Per-writer bytes in a file an un-adopted project commits are a merge conflict
    between two clones (found by tests/test_multiuser_merge_model.py)."""
    lock = tmp_path / "events.lock"
    for _ in range(3):
        with _flock(lock, 1):
            pass
    assert lock.read_bytes() == b""


def test_holder_lookup_never_raises_and_degrades_to_nothing(tmp_path, monkeypatch):
    lock = tmp_path / "events.lock"
    lock.write_text("")
    real_read = Path.read_text

    def broken(self, *a, **k):
        if str(self) == "/proc/locks":
            raise PermissionError("denied")
        return real_read(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", broken)
    assert L._holder_note(lock) == ""
    assert L._holder_note(tmp_path / "missing.lock") == ""


def test_a_malformed_proc_locks_line_is_skipped(tmp_path, monkeypatch):
    lock = tmp_path / "events.lock"
    lock.write_text("")
    real_read = Path.read_text

    def junk(self, *a, **k):
        if str(self) == "/proc/locks":
            return "garbage\n1: FLOCK ADVISORY WRITE notapid 00:00:1 0 EOF\n"
        return real_read(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", junk)
    assert L._holder_note(lock) == ""

"""B184: a lock timeout names the holder and the wait, so overload is told from a wedge."""

import subprocess
import sys
import time

import pytest

from ddflow.infra.log import _flock

HOLDER = """
import sys, time
from pathlib import Path
from ddflow.infra.log import _flock
with _flock(Path(sys.argv[1]), 5):
    print("held", flush=True)
    time.sleep(30)
"""


def test_timeout_reports_wait_and_holder(tmp_path):
    lock = tmp_path / "events.lock"
    p = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(lock)], stdout=subprocess.PIPE, text=True
    )
    try:
        assert p.stdout.readline().strip() == "held"
        t0 = time.monotonic()
        with pytest.raises(TimeoutError) as e, _flock(lock, 0.3):
            pass
        msg = str(e.value)
        assert time.monotonic() - t0 < 5
        assert f"pid {p.pid}" in msg and "alive" in msg, msg
        assert "waited 0." in msg or "waited 1." in msg, msg
        assert "overloaded" in msg
    finally:
        p.kill()
        p.wait()


def test_lock_still_works_after_a_note_was_left(tmp_path):
    lock = tmp_path / "events.lock"
    for _ in range(3):
        with _flock(lock, 1):
            pass
    assert lock.read_text().startswith("pid ")

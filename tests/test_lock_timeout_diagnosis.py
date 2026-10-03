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
        assert f"pid {p.pid} (alive" in msg and "NOT running" not in msg, msg
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


def test_a_dead_holders_note_says_so(tmp_path):
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    lock = tmp_path / "events.lock"
    lock.write_text(f"pid {dead.pid} 1700000000.0 ddflow task add X\n")
    from ddflow.infra.log import _holder_note

    note = _holder_note(lock)
    assert f"pid {dead.pid} (NOT running" in note and "ddflow task add X" in note, note
    assert "it may have released" in note


@pytest.mark.parametrize(
    "junk",
    [
        "",
        "garbage",
        "pid",
        "pid \u00b2 1700000000 cmd",  # isdigit() but not an int
        "pid 99999999999999999999999 1 cmd",  # too large for kill()
        "pid 123 not-a-number cmd",
        "pid " + "9" * 5000 + " 1 cmd",  # past int()'s digit limit
        b"\x00\xff\xfe pid",  # not valid UTF-8
    ],
)
def test_a_junk_lock_file_never_raises_out_of_the_timeout_path(tmp_path, junk):
    from ddflow.infra.log import _holder_note

    lock = tmp_path / "events.lock"
    lock.write_bytes(junk if isinstance(junk, bytes) else junk.encode("utf-8"))
    assert isinstance(_holder_note(lock), str)


@pytest.mark.parametrize("junk", ["pid -1 0 evil", "pid 0 0 evil", "pid 1_0 0 evil", "pid +12 0 x"])
def test_a_pid_that_is_not_a_plain_positive_number_is_no_lead(tmp_path, junk):
    from ddflow.infra.log import _holder_note

    lock = tmp_path / "events.lock"
    lock.write_text(junk)
    assert _holder_note(lock) == ""


def test_an_older_longer_note_is_trimmed_not_left_as_a_tail(tmp_path):
    lock = tmp_path / "events.lock"
    lock.write_text("pid 1 0 " + "x" * 900 + "\n")  # what the first version could leave
    with _flock(lock, 1):
        pass
    assert lock.stat().st_size == 256 and "xxxx" not in lock.read_text()


def test_a_non_utf8_command_line_does_not_stop_the_lock_being_taken(tmp_path, monkeypatch):
    """sys.argv carries undecodable bytes as lone surrogates; encoding them used to raise
    out of the lock, so an ordinary invocation failed with no contention at all."""
    from ddflow.infra import log as L

    monkeypatch.setattr(L, "_ARGV", [])
    monkeypatch.setattr(sys, "argv", ["ddflow", "task", "add", "bad\udcffname"])
    lock = tmp_path / "events.lock"
    with _flock(lock, 1):
        pass
    assert lock.read_text().startswith("pid ")


def test_the_note_is_one_fixed_size_write(tmp_path):
    lock = tmp_path / "events.lock"
    for _ in range(3):
        with _flock(lock, 1):
            pass
    assert lock.stat().st_size == 256


def test_the_reader_never_shows_bytes_past_the_note(tmp_path):
    """The note is one line. Bytes after it (the tail of an older, longer note, seen in the
    window between a writer's pwrite and its trim) are not part of it."""
    from ddflow.infra.log import _holder_note

    lock = tmp_path / "events.lock"
    lock.write_bytes(b"pid 1 1700000000.0 ddflow new\n" + b"STALE-TAIL-OF-AN-OLDER-NOTE" * 5)
    out = _holder_note(lock)
    assert "ddflow new" in out and "STALE" not in out, out


def test_the_reader_stops_at_the_note_size_when_there_is_no_newline(tmp_path):
    from ddflow.infra.log import _holder_note

    lock = tmp_path / "events.lock"
    lock.write_bytes(b"pid 1 1700000000.0 " + b"Q" * 400)
    out = _holder_note(lock)
    assert out.count("Q") <= 200  # the command is cut, and never longer than the bound

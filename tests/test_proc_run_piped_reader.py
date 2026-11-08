"""proc_server.run_piped must not leak its stderr reader thread and pipe (bug B-run-piped-reader-outlives-child).

A descendant that left the process group can keep the pipe's write end open after the direct
child exits, so a reader blocked in ``read1`` never sees end of file and the daemon thread --
holding the ``Popen`` and its pipes -- lives until that descendant exits. The fix sets the
read end non-blocking and stops the reader with a stop flag on every exit path, then closes
the read end, so a finished run releases the thread and the fd instead of leaking them.
"""

from __future__ import annotations

import os
import threading

from ddflow.adapters import proc_server as PS

_READER_NAME = "run_piped-stderr"


class _Stderr:
    """A real stderr pipe whose write end the test holds open, as a detached descendant
    would. ``read1`` blocks (or, once ``run_piped`` sets the fd non-blocking, raises) while
    the write end is open and no data is there, so the reader cannot finish on its own."""

    def __init__(self) -> None:
        self._read, self._write = os.pipe()
        self.closed = False

    def fileno(self) -> int:
        return self._read

    def close(self) -> None:
        if not self.closed:
            os.close(self._read)
            self._read = -1
            self.closed = True

    def write(self, data: bytes) -> None:
        os.write(self._write, data)

    def close_write(self) -> None:
        os.close(self._write)


class _Proc:
    """A process that already exited: ``wait`` returns 0 and only ``stderr`` is left open."""

    def __init__(self) -> None:
        self.stderr = _Stderr()
        self.stdin = None
        self.stdout = None

    def poll(self) -> int:
        return 0

    def wait(self, timeout: float | None = None) -> int:
        return 0


def _reader_alive() -> bool:
    return any(t.name == _READER_NAME and t.is_alive() for t in threading.enumerate())


def _run(proc: _Proc, timeout: float = 30, stderr_bytes: int = 4096):
    return PS.run_piped(["true"], lambda p: None, timeout=timeout, stderr_bytes=stderr_bytes)


def test_a_descendant_holding_stderr_does_not_leak_the_reader(monkeypatch):
    """The regression: on return, the read end AND the reader thread must be gone -- a
    close-only fix would release the fd but leave the reader looping until the descendant
    lets go."""
    proc = _Proc()
    monkeypatch.setattr(PS, "spawn_stdio", lambda argv, **kw: proc)
    try:
        end = _run(proc)
        assert end.code == 0, end
        assert end.stderr == b"", end  # nothing is drained while the descendant is silent
        assert proc.stderr.closed, (
            "run_piped left stderr open while a descendant held the write end"
        )
        assert not _reader_alive(), "the stderr reader thread was left alive"
    finally:
        proc.stderr.close_write()


def test_the_head_keeps_capturing_stderr_up_to_the_bound(monkeypatch):
    """The non-blocking read is a leak fix, not a reader change: data the child wrote before
    it went quiet is still drained and still obeys ``stderr_bytes``."""
    proc = _Proc()
    monkeypatch.setattr(PS, "spawn_stdio", lambda argv, **kw: proc)
    proc.stderr.write(b"x" * 100)
    try:
        end = _run(proc, stderr_bytes=8)
        assert end.stderr == b"x" * 8, end
        assert proc.stderr.closed
        assert not _reader_alive()
    finally:
        proc.stderr.close_write()


def test_a_reader_that_sees_end_of_file_stops_but_the_fd_is_still_released(monkeypatch):
    """A reader that reaches end of file (every write end closed) stops on its own, and the
    read end is released on this common path too, not only when a descendant holds it."""
    proc = _Proc()
    monkeypatch.setattr(PS, "spawn_stdio", lambda argv, **kw: proc)
    proc.stderr.write(b"done\n")
    proc.stderr.close_write()
    end = _run(proc)
    assert end.code == 0, end
    assert end.stderr == b"done\n", end
    assert proc.stderr.closed, "run_piped left stderr open after a normal child exit"
    assert not _reader_alive()

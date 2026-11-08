"""proc_server.run_piped must not leak its stderr reader thread and pipe (bug B-run-piped-reader-outlives-child).

A descendant that left the process group can keep the pipe's write end open after the direct
child exits, so a reader blocked in ``read1`` never sees end of file and the daemon thread --
holding the ``Popen`` and its pipes -- lives until that descendant exits. The fix poll-reads
stderr (``select`` + a stop flag), so a finished run can stop the reader and release the read
end instead of blocking behind the descendant.
"""

from __future__ import annotations

import os

from ddflow.adapters import proc_server as PS


class _Stderr:
    """A real stderr pipe whose write end the test holds open, as a detached descendant
    would. ``select`` sees no data and no end of file until the write end is closed, so the
    only way the reader stops is the caller's release -- exactly the leak under test."""

    def __init__(self) -> None:
        self._read, self._write = os.pipe()
        self.closed = False

    def fileno(self) -> int:
        return self._read

    def read1(self, n: int) -> bytes:
        return b"" if self.closed else os.read(self._read, n)

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


def _run(proc: _Proc, timeout: float = 30, stderr_bytes: int = 4096):
    return PS.run_piped(
        ["true"], lambda p: None, timeout=timeout, stderr_bytes=stderr_bytes
    )


def test_a_descendant_holding_stderr_does_not_leak_the_pipe(monkeypatch):
    """The regression: once ``run_piped`` returns, the read end must be released -- left
    open, the daemon reader and the Popen's pipes leak until the descendant lets go."""
    proc = _Proc()
    monkeypatch.setattr(PS, "spawn_stdio", lambda argv, **kw: proc)
    try:
        end = _run(proc)
        assert end.code == 0, end
        assert end.stderr == b"", end  # nothing is drained while the descendant is silent
        assert proc.stderr.closed, "run_piped left stderr open while a descendant held the write end"
    finally:
        proc.stderr.close_write()


def test_the_head_keeps_capturing_stderr_up_to_the_bound(monkeypatch):
    """The poll is a leak fix, not a reader change: data the child wrote before it went quiet
    is still drained and still obeys ``stderr_bytes``."""
    proc = _Proc()
    monkeypatch.setattr(PS, "spawn_stdio", lambda argv, **kw: proc)
    proc.stderr.write(b"x" * 100)
    try:
        end = _run(proc, stderr_bytes=8)
        assert end.stderr == b"x" * 8, end
        assert proc.stderr.closed
    finally:
        proc.stderr.close_write()


def test_a_reader_that_sees_end_of_file_stops_on_its_own(monkeypatch):
    """A reader that reaches end of file (the child and every descendant closed the pipe)
    already stopped: nothing is left to close, and the drained head still comes back."""
    proc = _Proc()
    monkeypatch.setattr(PS, "spawn_stdio", lambda argv, **kw: proc)
    proc.stderr.write(b"done\n")
    proc.stderr.close_write()
    end = _run(proc)
    assert end.code == 0, end
    assert end.stderr == b"done\n", end

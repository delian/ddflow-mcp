"""proc_server.run_piped must not leak its stderr reader thread and pipe (bug B-run-piped-reader-outlives-child).

After ``p.wait()``, ``run_piped`` joins the stderr reader for 1 s and returns. A descendant
that left the process group can keep the pipe's write end open, so the reader blocks in
``read1`` forever, and the daemon thread -- holding the ``Popen`` and its pipes -- lives
until that descendant exits: one leak per such call in a long-running process. The fix
closes the pipe from the caller side after the join, so the blocked reader stops, while the
returned head still carries what it drained before it was cut off.
"""

from __future__ import annotations

import threading

from ddflow.adapters import proc_server as PS


class _Pipe:
    """A stderr pipe whose read end drains a click, then blocks until it is closed from the
    caller side, as if a descendant still held the write end open. ``read1`` must return
    ``b""`` once the caller closes the pipe (then the drain loop ends)."""

    def __init__(self, payload: bytes = b"noise\n"):
        self._payload = payload
        self._sent = False
        self._closed = threading.Event()
        self.closed = False

    def read1(self, n: int) -> bytes:
        if not self._sent:
            self._sent = True
            return self._payload
        self._closed.wait(30.0)  # stays blocked while the write end is open elsewhere
        return b""

    def close(self) -> None:
        self.closed = True
        self._closed.set()


class _Proc:
    """A process that already exited: ``poll`` says done, ``wait`` returns 0, and only the
    blocked reader would keep ``stderr`` open."""

    def __init__(self) -> None:
        self.stderr = _Pipe()
        self.stdin = None
        self.stdout = None

    def poll(self) -> int:
        return 0

    def wait(self, timeout: float | None = None) -> int:
        return 0


def _spawn(proc: _Proc):
    def spawn(argv, **kw):
        return proc

    return spawn


def test_a_blocked_stderr_reader_does_not_leak_the_pipe(monkeypatch):
    """The regression: once ``run_piped`` returns, the stderr pipe must be closed from the
    caller side so the blocked reader thread ends -- left open, it keeps the Popen and its
    pipes alive until the descendant lets go."""
    proc = _Proc()
    monkeypatch.setattr(PS, "spawn_stdio", _spawn(proc))

    end = PS.run_piped(["true"], lambda p: None, timeout=30)

    assert end.code == 0, end
    assert end.stderr == b"noise\n", end  # what the reader drained before it was cut off
    assert proc.stderr.closed, "run_piped left stderr open while the reader thread was blocked"


def test_the_head_keeps_the_stderr_bytes_bound(monkeypatch):
    """The close is a leak fix, not a reader change: the drained head still obeys
    ``stderr_bytes`` before the reader is cut off."""
    proc = _Proc(b"x" * 100)
    monkeypatch.setattr(PS, "spawn_stdio", _spawn(proc))

    end = PS.run_piped(["true"], lambda p: None, timeout=30, stderr_bytes=8)

    assert end.stderr == b"x" * 8, end
    assert proc.stderr.closed


def test_a_reader_that_reaches_eof_ends_without_a_close(monkeypatch):
    """A reader that sees end of file on its own (the child closed its pipe) already ended
    before the join: nothing is left to close, and the drained head still comes back."""
    proc = _Proc(b"done\n")

    class EofPipe:
        def __init__(self):
            self.closed = False

        def read1(self, n: int) -> bytes:
            return b""

        def close(self) -> None:
            self.closed = True

    proc.stderr = EofPipe()
    monkeypatch.setattr(PS, "spawn_stdio", _spawn(proc))

    end = PS.run_piped(["true"], lambda p: None, timeout=30)
    assert end.code == 0, end
    assert end.stderr == b"", end

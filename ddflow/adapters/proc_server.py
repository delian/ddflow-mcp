"""The long-lived server / stdio-relay half of the `Proc` family (ADP-migrate-homes).

`relay_stdio`, `run_piped`, `run_shell`, `kill_group` and the owner-watch watcher: the
subprocess machinery that runs a session of its own and relays or drains stdio. Moved out of
`ddflow.adapters.proc` so each file stays under the r-small-files bound; callers reach every
name through `ddflow.adapters.proc` / `ddflow.infra.proc`.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, NoReturn

from . import proc as _proc
from . import resources as R


def spawn_stdio(
    argv: list[str],
    *,
    cwd: Any = None,
    env: dict[str, str] | None = None,
    stderr: Any = subprocess.PIPE,
    text: bool = False,
) -> subprocess.Popen:
    """Start a long-lived server that is spoken to over stdin/stdout pipes, in a session
    of its own so `stop_group` reaches everything it started. May raise OSError."""
    return _proc.popen(
        argv,
        cwd=cwd,
        env=env,
        stdin=_proc.PIPE,
        stdout=_proc.PIPE,
        stderr=stderr,
        text=text,
        start_new_session=True,
    )


@dataclass
class PipedEnd:
    """How `run_piped` ended: ``answer`` when ``work`` ended it, else ``code`` and the head of
    ``stderr``. ``error`` is what kept it from starting; ``code`` below 0 is a kill."""

    answer: Any = None
    code: int | None = None
    stderr: bytes = b""
    error: OSError | None = None


def run_piped(
    argv: list[str],
    work: Callable[[subprocess.Popen], Any],
    *,
    timeout: float,
    stderr_bytes: int = 4096,
) -> PipedEnd:
    """Run ``argv`` in a session of its own with its binary pipes given to ``work``, the whole
    cut at ``timeout`` seconds. A ``work`` that returns a value ends the run there and then;
    None lets it go on to its exit status. stderr is drained alongside, so a chatty child
    cannot stall, and a descendant that keeps the pipe open cannot hold the answer back.
    Never signals a pid already reaped."""
    try:
        p = spawn_stdio(argv)
    except OSError as exc:
        return PipedEnd(error=exc)
    head = bytearray()
    stop = threading.Event()

    def drain() -> None:
        """Read stderr into ``head`` until end of file. The read end is made non-blocking so
        a finished run can stop the reader with ``stop`` (``os.read`` raises
        `BlockingIOError` instead of blocking behind a descendant that still holds the
        write end); where the platform cannot un-block the pipe it ends on end of file."""
        fd = p.stderr.fileno()  # type: ignore[union-attr]
        with contextlib.suppress(OSError):  # no O_NONBLOCK (e.g. a Windows pipe): stay blocking
            os.set_blocking(fd, False)
        while not stop.is_set():
            try:
                chunk = os.read(fd, 65536)
            except (BlockingIOError, InterruptedError):
                time.sleep(0.02)  # nothing is there yet: check the stop flag again
                continue
            except OSError:
                break  # the pipe is closed: stop draining
            if not chunk:
                break  # end of file: every write end is closed
            head.extend(chunk[: stderr_bytes - len(head)])

    reader = threading.Thread(target=drain, daemon=True, name="run_piped-stderr")
    reader.start()
    clock = threading.Timer(timeout, kill_group, (p,))
    clock.start()
    try:
        answer = work(p)
        if answer is not None:
            return PipedEnd(answer=answer)
        code = p.wait()
        reader.join(1)
        stop.set()
        reader.join(1)
        return PipedEnd(code=code, stderr=bytes(head))
    finally:
        clock.cancel()
        stop.set()
        reader.join(1)  # bounded: never hang on a reader the platform cannot stop
        with contextlib.suppress(ValueError, OSError):
            p.stderr.close()  # type: ignore[union-attr]
        if p.poll() is None:
            stop_group(p)


def pump_lines(src: Any, dst: Any, note: Callable[[str, bytes], None], direction: str) -> None:
    """Copy ``src`` to ``dst`` line by line until EOF, closing ``dst``. ``note`` sees each line
    BEFORE it passes, so a line is recorded before anything can answer it. A reader that
    went away ends the copy quietly: that is how such a session ends."""
    try:
        for line in iter(src.readline, b""):
            note(direction, line)
            dst.write(line)
            dst.flush()
    except (BrokenPipeError, ValueError):
        pass
    finally:
        with contextlib.suppress(OSError):
            dst.close()


def relay_stdio(argv: list[str], note: Callable[[str, bytes], None], *, grace_s: float) -> NoReturn:
    """Stand between this process's own stdio and ``argv``, then END this process with the
    child's exit code: its stdin goes to the child and the child's stdout comes back, ``note``
    seeing each line (direction ``in`` or ``out``), until the child closes its stdout.

    Unlike `run_piped` nothing cuts the run and stderr stays the caller's: a server lives as
    long as its session. The exit is 0 when the child had to be stopped, because its reader
    left and it did not exit within ``grace_s``. It is `os._exit`: the upstream copy is a
    daemon blocked on our stdin and a normal shutdown aborts on that lock; both copies flush
    every line. Raises OSError when the child cannot start."""
    child = spawn_stdio(argv, stderr=None)
    assert child.stdin is not None and child.stdout is not None
    args = (sys.stdin.buffer, child.stdin, note, "in")
    threading.Thread(target=pump_lines, args=args, daemon=True).start()
    pump_lines(child.stdout, sys.stdout.buffer, note, "out")
    try:
        code = child.wait(timeout=grace_s)
    except subprocess.TimeoutExpired:
        stop_group(child)
        code = 0
    os._exit(code)


def stop_group(p: subprocess.Popen, *, reap_s: float = 10) -> None:
    """Kill ``p`` and everything it started, then reap it (waiting at most ``reap_s``)."""
    kill_group(p)
    with contextlib.suppress(subprocess.TimeoutExpired):
        p.wait(timeout=reap_s)


def kill_group(p: subprocess.Popen, sig: int | None = None) -> None:
    """Signal ``p`` AND everything it started: its whole process group (``sig``; SIGKILL when None,
    SIGTERM to ask it to stop first). SIGKILL is named only on POSIX: Windows has no such signal.

    ``p`` must have been started in a session of its own (`run_shell` does), so the
    group is its pid; call it before ``p`` is reaped, while that pid -- and so the group
    id -- is still reserved and cannot name a recycled process. Windows has no process
    groups: `taskkill /T` ends the tree, and `kill` the shell if that could not run.
    """
    if os.name == "posix":
        try:
            os.killpg(p.pid, signal.SIGKILL if sig is None else sig)
            return
        except (ProcessLookupError, PermissionError):
            pass  # gone already, or not ours: the direct child is all we can still reach
    else:
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            _proc.run(
                ["taskkill", "/F", "/T", "/PID", str(p.pid)],
                capture_output=True,
                timeout=_proc.TIMEOUTS["instant"],
            )
    with contextlib.suppress(OSError):
        if sig is None or sig == getattr(signal, "SIGKILL", None):
            p.kill()
        else:
            p.terminate()


@dataclass
class ShellResult:
    """How a shell line ended, as a value (never an exception).

    ``code`` is the exit status, None when the command did not finish (``timed_out``) or
    never started (``could_not_run``: no such directory, an environment the OS refused).
    The two are what a caller must not read as "the command failed": a tool that quietly
    stopped being installed, or a suite killed at its bound, says nothing about the code.
    """

    code: int | None
    out: str = ""
    err: str = ""
    timed_out: bool = False
    could_not_run: bool = False
    #: Seconds from start to the end (or the kill).
    elapsed_s: float = 0.0
    #: What the command cost (`resources.Resources`); None when it never started.
    resources: R.Resources | None = None

    @property
    def ok(self) -> bool:
        return self.code == 0

    @property
    def finished(self) -> bool:
        """The command ran to an exit status of its own."""
        return self.code is not None

    @property
    def output(self) -> str:
        return self.out + self.err


def run_shell(
    command: str,
    *,
    timeout_s: float | None,
    cwd: Any = None,
    env: dict[str, str] | None = None,
    on_tick: Callable[[], None] | None = None,
    tick_s: float = 0,
    merge_stderr: bool = False,
    stdin_text: str | None = None,
) -> ShellResult:
    """Run an operator's shell line to completion and report how it ended.

    The line leads a session of its own, so a timeout kills its whole process group
    (Bed0f5b6d99); the output is text decoded with ``replace``; stdin is /dev/null.
    ``on_tick`` is called every ``tick_s`` seconds meanwhile, on this thread. With
    ``merge_stderr`` stderr is folded into ``out`` in the order written. This never raises
    for the command's own trouble: a timeout is ``timed_out``, a command that could not be
    started is ``could_not_run`` with the reason in ``err``. (A bad ``on_tick``/``tick_s``
    pair is the caller's bug and still raises ValueError, and an exception raised BY
    ``on_tick`` is not the command's trouble: the group is killed and that exception
    reaches the caller as it was, never as ``could_not_run`` or ``timed_out``.) With
    ``stdin_text`` the command is fed that text on stdin and then sees end of file (a
    companion that takes a JSON request); without it stdin stays /dev/null.
    """
    if on_tick is not None and tick_s <= 0:
        raise ValueError("run_shell: on_tick needs tick_s > 0, or it would never be called")
    start = time.monotonic()
    kwargs: dict[str, Any] = {
        "cwd": cwd,
        "env": env,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if merge_stderr:
        kwargs["stderr"] = subprocess.STDOUT
    from_tick: list[Exception] = []
    cost: list[R.Resources] = []

    def tick() -> None:
        assert on_tick is not None
        try:
            on_tick()
        except Exception as exc:  # the callback's own trouble is not the command's
            from_tick.append(exc)
            raise

    try:
        p = _shell_group(
            command,
            timeout=timeout_s,
            on_tick=tick if on_tick is not None else None,
            tick_s=tick_s,
            input=stdin_text,
            cost=cost,
            **kwargs,
        )
    except (subprocess.TimeoutExpired, OSError, ValueError) as exc:
        if from_tick:
            raise from_tick[0] from None  # the callback's error, as it was; not the command's
        if isinstance(exc, subprocess.TimeoutExpired):
            return ShellResult(
                None,
                _text(exc.output),
                _text(exc.stderr),
                timed_out=True,
                elapsed_s=round(time.monotonic() - start, 3),
                resources=cost[0] if cost else None,
            )
        return ShellResult(None, err=str(exc), could_not_run=True)
    return ShellResult(
        p.returncode,
        p.stdout or "",
        p.stderr or "",
        elapsed_s=round(time.monotonic() - start, 3),
        resources=cost[0] if cost else None,
    )


def _text(raw: Any) -> str:
    if raw is None:
        return ""
    return raw if isinstance(raw, str) else raw.decode("utf-8", "replace")


def _shell_group(
    command: str,
    *,
    timeout: float | None,
    on_tick: Callable[[], None] | None = None,
    tick_s: float = 0,
    input: str | None = None,
    cost: list[R.Resources] | None = None,
    **kwargs: Any,
) -> subprocess.CompletedProcess:
    """`run(command, shell=True, capture_output=True, timeout=...)` whose timeout kills
    the command's whole process group, not just the shell (Bed0f5b6d99).

    `subprocess.run` kills only the shell on timeout: a test runner it started, and that
    runner's workers, kept running orphaned -- using CPU and writing into a worktree that
    may be removed next. Here the shell leads a new session, so on timeout the group is
    killed (`kill_group`), the output drained and the child reaped, and TimeoutExpired is
    raised as `subprocess.run` raises it. Any other exception (KeyboardInterrupt) kills
    the group too.

    ``on_tick`` is called every ``tick_s`` seconds while the command runs, on THIS
    thread (a long gate renews its lease that way; see `run_command_gate`).
    Output is captured unless ``capture_output=False`` or ``stdout``/``stderr`` say
    otherwise; the other keywords go to `popen` (stdin stays /dev/null unless ``input`` is
    given: a writer thread feeds that text across as many ticks as the child takes to read
    it, and then closes the pipe: see `_feed_stdin`).
    On timeout the TimeoutExpired carries everything the command wrote, as
    `subprocess.run`'s does (a resumed `communicate` returns all it accumulated).

    ``input`` is NOT handed to `communicate`: on Python 3.12.3 a resumed `communicate`
    (``input=None``) never re-registers stdin for writing, so a request larger than the
    pipe stopped after its first 64 KiB and a late reader hung until the timeout
    (B-proc-stdin-resume). A daemon thread writes it to the pipe instead, and the group's
    kill (EPIPE) ends the writer if the child never reads.
    """
    if on_tick is not None and tick_s <= 0:
        raise ValueError("run_shell: on_tick needs tick_s > 0, or it would never be called")
    if kwargs.pop("capture_output", True):
        kwargs.setdefault("stdout", _proc.PIPE)
        kwargs.setdefault("stderr", _proc.PIPE)
    if os.name == "posix":
        kwargs["start_new_session"] = True
    if input is not None:
        kwargs["stdin"] = _proc.PIPE
    watcher = _start_watcher()
    before = R.children_snapshot()
    try:
        p = _proc.popen(command, cls=_proc.MeteredPopen, shell=True, **kwargs)  # nosec B604 - the operator's line
    except BaseException:
        _arm_watcher(watcher, None)
        raise
    _arm_watcher(watcher, p)
    deadline = None if timeout is None else time.monotonic() + timeout
    try:
        if input is not None:
            _feed_stdin(p, input)
        while True:
            wait = None if deadline is None else max(0.0, deadline - time.monotonic())
            if on_tick is not None:
                wait = tick_s if wait is None else min(tick_s, wait)
            try:
                out, err = p.communicate(timeout=wait)
                _note_cost(cost, p, before)
                return subprocess.CompletedProcess(command, p.returncode, out, err)
            except subprocess.TimeoutExpired:
                if deadline is not None and time.monotonic() >= deadline:
                    raise
                if on_tick is not None:
                    on_tick()
    except subprocess.TimeoutExpired:
        kill_group(p)
        out, err = _drain(p)
        _note_cost(cost, p, before)
        raise subprocess.TimeoutExpired(command, timeout or 0, output=out, stderr=err) from None
    except BaseException:
        kill_group(p)
        _drain(p)
        raise


def _note_cost(cost: list[R.Resources] | None, p: subprocess.Popen, before: Any) -> None:
    """Append what the reaped ``p`` cost to ``cost`` (the caller's out-parameter), if it asked."""
    if cost is not None and p.returncode is not None:
        cost.append(R.measure(p, before))


#: The watcher: started BY the owner BEFORE the command and told the command's process group on
#: its stdin once that exists, so the only moment the command runs unwatched is the pipe write
#: after it starts: microseconds (bug Bdced4d9917). It polls whether the owner is still its
#: parent; when the owner is gone it kills the group until it is gone, and it ends when the
#: group is gone (or at once if the owner died before it was told a group). The last argv element
#: names it for `ps` and the tests.
_WATCHER = (
    "import os, signal, sys, time\n"
    "owner = int(sys.argv[1])\n"
    "line = sys.stdin.readline()\n"
    "if not line.strip():\n"
    "    sys.exit(0)\n"
    "group = int(line)\n"
    "def alive():\n"
    "    try:\n"
    "        os.killpg(group, 0)\n"
    "    except OSError:\n"
    "        return False\n"
    "    return True\n"
    "while os.getppid() == owner and alive():\n"
    "    time.sleep(1)\n"
    "for _ in range(50):\n"
    "    try:\n"
    "        os.killpg(group, signal.SIGKILL)\n"
    "    except ProcessLookupError:\n"
    "        break\n"
    "    except OSError:\n"
    "        pass\n"
    "    time.sleep(0.2)\n"
    "# ddflow-owner-watch\n"
)


def _start_watcher() -> subprocess.Popen | None:
    """Start the watcher of the command about to start (Ba3b7509530: a suite ran 8.3 hours after
    the gate run that started it was killed, and nothing reaped it). A SIGKILLed owner cannot
    clean up after itself, so a small process outside the group does it. Best effort: a
    watcher that cannot start leaves the old behaviour."""
    if os.name != "posix":
        return None
    try:
        w = subprocess.Popen(  # nosec B603 - our own interpreter and a fixed script
            [sys.executable, "-c", _WATCHER, str(os.getpid()), "ddflow-owner-watch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
            text=True,
        )
    except (OSError, ValueError):
        return None
    # Reaped when it ends (a long-lived owner must not collect a zombie per command).
    threading.Thread(target=w.wait, name="owner-watch-reaper", daemon=True).start()
    return w


def _arm_watcher(w: subprocess.Popen | None, p: subprocess.Popen | None) -> None:
    """Tell the watcher which group to guard (``p`` None: the command never started)."""
    if w is None or w.stdin is None:
        return
    with contextlib.suppress(OSError, ValueError):
        try:
            if p is not None and os.getpgid(p.pid) == p.pid:  # it leads a group of its own
                w.stdin.write(f"{p.pid}\n")
        finally:
            w.stdin.close()


def _feed_stdin(p: subprocess.Popen, text: str | bytes) -> None:
    """Write ``text`` to ``p``'s stdin on a daemon thread, then close it (end of file).

    The pipe is taken off ``p`` so `communicate` leaves it alone (it would close it before
    the first write, or, resumed, fail to keep writing: B-proc-stdin-resume). The text is
    encoded HERE, on the caller's thread, so a character the pipe's codec refuses raises to
    the caller as `communicate(input=)` did, never inside the writer where nobody sees it.
    Only a broken pipe ends the writer quietly: the child exited, or was killed, before it
    read everything.
    """
    pipe = p.stdin
    p.stdin = None
    if pipe is None:
        return
    raw = getattr(pipe, "buffer", pipe)
    data = text
    if isinstance(text, str):
        if raw is pipe:  # a binary pipe: what `communicate(input=str)` refused, loudly
            raise TypeError("run_shell: a str on stdin needs text mode (text=True)")
        data = text.encode(pipe.encoding, pipe.errors)

    def write() -> None:
        with contextlib.suppress(OSError):
            try:
                raw.write(data)
                raw.flush()
            finally:
                with contextlib.suppress(OSError):
                    pipe.close()

    threading.Thread(target=write, name="run_shell-stdin", daemon=True).start()


def _drain(p: subprocess.Popen) -> tuple[Any, Any]:
    """Everything ``p`` wrote (including what an earlier, timed-out `communicate` had
    read), and ``p`` reaped. Bounded: a grandchild that left the group (its own `setsid`)
    can hold the pipes open forever."""
    try:
        return p.communicate(timeout=_proc.TIMEOUTS["drain"])
    except subprocess.TimeoutExpired:
        for f in (p.stdin, p.stdout, p.stderr):
            if f is not None:
                with contextlib.suppress(OSError):
                    f.close()
        with contextlib.suppress(subprocess.TimeoutExpired):
            p.wait(timeout=_proc.TIMEOUTS["drain"])
        return None, None

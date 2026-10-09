"""Subprocess helpers whose only job is to keep a child's hands off our stdin.

ddflow runs as an **MCP server over stdio**: the JSON-RPC session IS this process's
stdin and stdout. `subprocess.run(...)` without an explicit `stdin=` hands the child
that same pipe, so any child that reads stdin — a shell gate, a reviewer CLI, an `npx`
that wants to prompt, a git command in an unexpected mode — consumes the bytes the
protocol needed, or closes the descriptor outright.

The failure is as quiet as it gets: the server exits **zero**, with an empty stderr,
and the client sees a closed stream with nothing to explain it. It was found when a
companion-detection probe added to `ddflow setup` ended the MCP session on the very
next tool call.

So every subprocess in this package goes through here, and the default is
``stdin=DEVNULL``. A child that wants input must say so explicitly, which no caller
here does.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

#: Re-exported so callers need only import this module.
DEVNULL = subprocess.DEVNULL
PIPE = subprocess.PIPE
CalledProcessError = subprocess.CalledProcessError
SubprocessError = subprocess.SubprocessError
TimeoutExpired = subprocess.TimeoutExpired
Popen = subprocess.Popen


def run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
    """`subprocess.run` with stdin detached unless the caller insists otherwise.

    ``input=`` is left alone: it already gives the child its own pipe, and setting both
    is a ValueError. That combination is the legitimate case — a reviewer CLI fed a
    diff on stdin — and it is safe precisely because the child is not holding ours.

    The test keys off the **value**, not the key. `"input" not in kwargs` left a hole
    at `proc.run(cmd, input=None)`: the guard declined to set `stdin`, and
    `subprocess.run`'s own `if input is not None` also declined, so the child inherited
    fd 0 — the JSON-RPC stream — through the shim written to make that impossible.
    `input=None` is the natural shape of "no diff to send", so the hole sits exactly
    where a caller would fall into it.
    """
    if kwargs.get("input") is None:
        kwargs.setdefault("stdin", subprocess.DEVNULL)
    return subprocess.run(*args, **kwargs)


def popen(*args: Any, **kwargs: Any) -> subprocess.Popen:
    """`subprocess.Popen` with the same default."""
    kwargs.setdefault("stdin", subprocess.DEVNULL)
    return subprocess.Popen(*args, **kwargs)


def spawn_shell(
    command: str,
    *,
    env: dict[str, str] | None = None,
    cwd: Any = None,
    stdin: Any = subprocess.DEVNULL,
    stdout: Any = subprocess.PIPE,
    stderr: Any = subprocess.PIPE,
) -> subprocess.Popen:
    """Start an operator's shell line without waiting: text mode, a session of its own (so
    `kill_group` reaches everything it started), stdin detached unless ``stdin`` says
    otherwise. The streaming counterpart of `run_shell`; may raise OSError/ValueError."""
    return subprocess.Popen(
        command,
        shell=True,  # nosec B602 B604 -- the operator's configured shell line
        cwd=cwd,
        env=env,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        text=True,
        start_new_session=True,
    )


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
            run(
                ["taskkill", "/F", "/T", "/PID", str(p.pid)],
                capture_output=True,
                timeout=TIMEOUTS["instant"],
            )
    with contextlib.suppress(OSError):
        if sig is None or sig == getattr(signal, "SIGKILL", None):
            p.kill()
        else:
            p.terminate()


#: Every named timeout in seconds, in one table (D-unify: one process layer). A call site
#: names the entry that describes it; the number is changed here, once.
TIMEOUTS: dict[str, int] = {
    #: a git command (`infra.git.run`'s default); a merge, a worktree add
    "git": 300,
    #: a git path listing (`infra.git.git_paths`)
    "git_listing": 60,
    #: a quick probe of the environment: `git config`, `rev-parse`, a version flag
    "probe": 30,
    #: a call that must answer at once: a process-table read, a taskkill
    "instant": 10,
    #: a command gate or a `[ci]` run that sets no timeout of its own
    "gate": 1800,
    #: a project's own test suite run for a baseline (onboarding)
    "suite_baseline": 900,
    #: the grace after a kill before giving up on draining a child's pipes
    "drain": 10,
}


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
            )
        return ShellResult(None, err=str(exc), could_not_run=True)
    return ShellResult(
        p.returncode,
        p.stdout or "",
        p.stderr or "",
        elapsed_s=round(time.monotonic() - start, 3),
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
    given: that text is handed to the first wait, which writes it across as many ticks as the child
    takes to read it, and then closes the pipe).
    On timeout the TimeoutExpired carries everything the command wrote, as
    `subprocess.run`'s does (a resumed `communicate` returns all it accumulated).
    """
    if on_tick is not None and tick_s <= 0:
        raise ValueError("run_shell: on_tick needs tick_s > 0, or it would never be called")
    if kwargs.pop("capture_output", True):
        kwargs.setdefault("stdout", PIPE)
        kwargs.setdefault("stderr", PIPE)
    if os.name == "posix":
        kwargs["start_new_session"] = True
    if input is not None:
        kwargs["stdin"] = PIPE
    p = popen(command, shell=True, **kwargs)  # nosec B604 - callers pass their operator's line
    deadline = None if timeout is None else time.monotonic() + timeout
    try:
        while True:
            wait = None if deadline is None else max(0.0, deadline - time.monotonic())
            if on_tick is not None:
                wait = tick_s if wait is None else min(tick_s, wait)
            try:
                out, err = p.communicate(input=input, timeout=wait)
                return subprocess.CompletedProcess(command, p.returncode, out, err)
            except subprocess.TimeoutExpired:
                if deadline is not None and time.monotonic() >= deadline:
                    raise
                input = None  # already handed to communicate(); a resumed wait takes none
                if on_tick is not None:
                    on_tick()
    except subprocess.TimeoutExpired:
        kill_group(p)
        out, err = _drain(p)
        raise subprocess.TimeoutExpired(command, timeout or 0, output=out, stderr=err) from None
    except BaseException:
        kill_group(p)
        _drain(p)
        raise


def _drain(p: subprocess.Popen) -> tuple[Any, Any]:
    """Everything ``p`` wrote (including what an earlier, timed-out `communicate` had
    read), and ``p`` reaped. Bounded: a grandchild that left the group (its own `setsid`)
    can hold the pipes open forever."""
    try:
        return p.communicate(timeout=TIMEOUTS["drain"])
    except subprocess.TimeoutExpired:
        for f in (p.stdin, p.stdout, p.stderr):
            if f is not None:
                with contextlib.suppress(OSError):
                    f.close()
        with contextlib.suppress(subprocess.TimeoutExpired):
            p.wait(timeout=TIMEOUTS["drain"])
        return None, None

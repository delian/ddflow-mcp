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
from typing import Any

#: Re-exported so callers need only import this module.
DEVNULL = subprocess.DEVNULL
PIPE = subprocess.PIPE
CalledProcessError = subprocess.CalledProcessError
SubprocessError = subprocess.SubprocessError
TimeoutExpired = subprocess.TimeoutExpired


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


def kill_group(p: subprocess.Popen) -> None:
    """Kill ``p`` AND everything it started: its whole process group.

    ``p`` must have been started in a session of its own (`run_shell` does), so the
    group is its pid; call it before ``p`` is reaped, while that pid -- and so the group
    id -- is still reserved and cannot name a recycled process. Windows has no process
    groups: `taskkill /T` ends the tree, and `kill` the shell if that could not run.
    """
    if os.name == "posix":
        try:
            os.killpg(p.pid, signal.SIGKILL)
            return
        except (ProcessLookupError, PermissionError):
            pass  # gone already, or not ours: the direct child is all we can still reach
    else:
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True, timeout=10)
    with contextlib.suppress(OSError):
        p.kill()


def run_shell(
    command: str,
    *,
    timeout: float | None,
    on_tick: Callable[[], None] | None = None,
    tick_s: float = 0,
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
    thread (a long gate renews its lease that way; see `gates._run_ticking`).
    Output is captured unless ``capture_output=False`` or ``stdout``/``stderr`` say
    otherwise; the other keywords go to `popen`.
    """
    if kwargs.pop("capture_output", True):
        kwargs.setdefault("stdout", PIPE)
        kwargs.setdefault("stderr", PIPE)
    data = kwargs.pop("input", None)
    if data is not None:
        kwargs["stdin"] = PIPE
    if os.name == "posix":
        kwargs["start_new_session"] = True
    p = popen(command, shell=True, **kwargs)  # nosec B604 - callers pass their operator's line
    deadline = None if timeout is None else time.monotonic() + timeout
    try:
        while True:
            wait = None if deadline is None else max(0.0, deadline - time.monotonic())
            if on_tick is not None and tick_s > 0:
                wait = tick_s if wait is None else min(tick_s, wait)
            try:
                out, err = p.communicate(input=data, timeout=wait)
                return subprocess.CompletedProcess(command, p.returncode, out, err)
            except subprocess.TimeoutExpired:
                if deadline is not None and time.monotonic() >= deadline:
                    raise
                data = None  # already written: communicate keeps feeding the rest
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
    """What ``p`` wrote before it was killed, and ``p`` reaped. Bounded: a grandchild that
    left the group (its own `setsid`) can hold the pipes open forever."""
    try:
        return p.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        for f in (p.stdin, p.stdout, p.stderr):
            if f is not None:
                with contextlib.suppress(OSError):
                    f.close()
        with contextlib.suppress(subprocess.TimeoutExpired):
            p.wait(timeout=10)
        return None, None

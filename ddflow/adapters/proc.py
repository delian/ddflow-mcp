"""The `Proc` port and its default adapter: run subprocesses with stdin detached from the MCP
stdio session.

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

The long-lived server / stdio-relay helpers (`relay_stdio`, `run_piped`, `run_shell`,
`kill_group`, the owner-watch) live in `ddflow.adapters.proc_server`, beside this module,
so each file stays under the r-small-files bound (ADP-migrate-homes). `ddflow.infra.proc`
re-exports both.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from . import resources as R
from .ports import port

#: Re-exported so callers need only import this module.
DEVNULL = subprocess.DEVNULL
PIPE = subprocess.PIPE
CalledProcessError = subprocess.CalledProcessError
SubprocessError = subprocess.SubprocessError
TimeoutExpired = subprocess.TimeoutExpired
Popen = subprocess.Popen
CompletedProcess = subprocess.CompletedProcess
#: `Popen` whose reap keeps its own rusage (see `resources.metered`).
MeteredPopen = R.metered(subprocess.Popen)


@port
@runtime_checkable
class Proc(Protocol):
    """Run subprocesses with stdin detached from the MCP stdio session.

    Home: `ddflow.adapters.proc` (compat re-export `ddflow.infra.proc`). Single adapter; a
    second process runner is a bug.
    """

    def run(self, *args: Any, **kwargs: Any) -> Any:
        """`subprocess.run` with `stdin=DEVNULL` by default; the stdlib result."""

    def capture(self, argv: list[str], *, timeout: float | None, **kwargs: Any) -> Any:
        """Run `argv` captured as text, never raising for the command's own trouble; the
        `Captured` value's `error` carries it."""

    def spawn_shell(self, line: str, **kwargs: Any) -> Any:
        """One shell line; a `ShellResult` whose `ok`/`finished` never read a missing
        binary as a failed command."""


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


def replace_process(path: str, argv: list[str], env: dict[str, str]) -> None:
    """Replace this process with ``path`` (same pid, same stdio), `os.execve`. Returns only by
    raising `OSError`: the one `exec` in the package, here so the stdin-guard ratchet has one
    place to look. Flush what the caller owns first; nothing runs after this succeeds."""
    os.execve(path, argv, env)


def popen(*args: Any, cls: type = subprocess.Popen, **kwargs: Any) -> subprocess.Popen:
    """`subprocess.Popen` (or ``cls``, a subclass: `resources.MeteredPopen`) with the same default."""
    kwargs.setdefault("stdin", subprocess.DEVNULL)
    return cls(*args, **kwargs)


@dataclass
class Captured:
    """How an argv run ended, as a value: `capture`'s answer.

    ``returncode``/``stdout``/``stderr`` are what `subprocess.run` would hold (None and
    empty when the command did not finish); ``error`` is the exception that stopped it
    (a `TimeoutExpired` when ``timed_out``, else the OSError / SubprocessError that
    kept it from running), None when it ran to an exit status.
    """

    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    error: BaseException | None = None
    #: What the command cost (`resources.Resources`); None when it never started.
    resources: R.Resources | None = None

    @property
    def timed_out(self) -> bool:
        return isinstance(self.error, subprocess.TimeoutExpired)

    @property
    def ran(self) -> bool:
        return self.error is None

    def unwrap(self) -> Captured:
        """``self`` when it ran; else raise ``error`` as `run` would have."""
        if self.error is not None:
            raise self.error
        return self


def capture(
    argv: list[str],
    *,
    timeout: float | None,
    cwd: Any = None,
    env: dict[str, str] | None = None,
    input: str | None = None,
    encoding: str | None = None,
    errors: str | None = None,
) -> Captured:
    """Run ``argv`` to completion with its output captured as text, never raising for the
    command's own trouble: a timeout, a missing binary or an OSError comes back in
    ``error`` (`unwrap` raises it for a caller that lets it through). stdin is /dev/null
    unless ``input`` is given (see `run`)."""
    before = R.children_snapshot()
    try:
        done = run(
            argv,
            cwd=cwd,
            env=env,
            input=input,
            capture_output=True,
            text=True,
            encoding=encoding,
            errors=errors,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:  # it started and was killed: what it cost, as a delta
        return Captured(error=exc, resources=R.measure(None, before))
    except (subprocess.SubprocessError, OSError) as exc:
        return Captured(error=exc)
    # `run` is the seam the call sites patch, and `subprocess.run` hands back no Popen, so
    # there is no wait4 here: the RUSAGE_CHILDREN delta, marked approximate (`resources.DELTA`).
    return Captured(
        done.returncode,
        done.stdout or "",
        done.stderr or "",
        resources=R.measure(None, before),
    )


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


#: Every named timeout in seconds, in one table (D-unify: one process layer). A call site
#: names the entry that describes it; the number is changed here, once.
TIMEOUTS: dict[str, int] = {
    #: a git command (`adapters.git.run`'s default); a merge, a worktree add
    "git": 300,
    #: a git path listing (`adapters.git.git_paths`)
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

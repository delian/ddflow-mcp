"""One runner for the commands an operator configured (D-unify, B-uni-cmdrunner).

Command gates, the CI gate and (as they land) docs lint and extractors, `cmd:` sources, the
embedder, schedule jobs, security scanners, skill installs and self-upgrade each ran an
operator's shell line and each re-decided the same five things: how long it may run, what
"the tool is not installed" looks like, how many may run at once, how much output is kept
and what it may leak. This module is the one place that decides them.

* **Declared, not composed.** `run` takes a `Declared` line -- text an operator wrote in
  gates.toml, `[ci].command` or another config file, with the place it came from -- and
  refuses a bare string. A line ddflow assembled from data (an agent's argument, a finding's
  text) has no business here; it goes through `infra.proc.run` as an argv.
* **Unavailable is never passed.** A binary that is not installed, a timeout, a command that
  could not be started, the shell's own "not found" and a full admission queue are each an
  *unavailable* result with the reason; only a command that ran has an exit code (the shell's
  own "not found" keeps the 126/127 it reported), and only the caller decides what that code
  means.
* **Admission.** With ``slots`` the run holds one `Slots` slot for its duration, so
  background work and gates share one bound on concurrent processes.
* **Output.** The result keeps what the command wrote, optionally clipped, per stream, to ``max_output``
  characters (the tail: a suite's verdict is at its end) and passed through a `Redactor`.
  The digest and the length are of the whole raw output, so a kept log can be checked
  against them whatever the caller later shows.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.digest import content_digest
from ..core.redact import Redactor
from ..core.textcut import clip
from ..infra import proc as P
from .slots import Slots, SlotsTimeout

#: Why a run did not happen (`CommandRun.kind` when `status` is "unavailable").
MISSING = "missing"  # the program is not installed
TIMEOUT = "timeout"
COULD_NOT_RUN = "could_not_run"  # the process could not be started
NOT_FOUND = "not_found"  # the shell itself reported 126/127 "not found"
BUSY = "busy"  # every admission slot was held for the whole wait

_ENV_WORD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
SHELL_META = set(";|&<>()$`\n")

#: Builtins and compound-command words that legitimately have no PATH entry. `exit`, `true`
#: and `false` appear in real gate configs and in this project's own tests.
SHELL_WORDS = frozenset(
    {
        "exit",
        "true",
        "false",
        "cd",
        "echo",
        "test",
        "[",
        "[[",
        "((",
        ":",
        "set",
        "unset",
        "export",
        "eval",
        "source",
        ".",
        "read",
        "wait",
        "trap",
        "shift",
        "return",
        "{",
        "!",
        "if",
        "for",
        "while",
        "until",
        "case",
        "select",
        "function",
        "time",
        "command",
        "builtin",
        "exec",
        "type",
        "hash",
        "alias",
        "unalias",
        "let",
        "local",
        "declare",
        "readonly",
        "umask",
        "ulimit",
        "printf",
        "pwd",
        "kill",
        "getopts",
        "break",
        "continue",
    }
)


def executable_missing(command: str, path: str | None = None) -> str:
    """The leading program of ``command`` when it is a plain word and not installed, else "".

    "" means *no opinion*: an empty line, one that opens with shell syntax, a builtin or
    compound word, a path (the exec decides), or text `shlex` cannot split (the shell will
    say so). Leading ``VAR=value`` words are environment, not the program. Being sure only
    about the easy case is the point: a guess about compound shell would produce false
    UNAVAILABLEs, which stall a pipeline as surely as a false pass corrupts one.

    ``path`` is the ``PATH`` the command will run under (default: this process's); an
    environment handed to the child without a ``PATH`` leaves it the default search path.
    """
    cmd = command.strip()
    if not cmd or cmd[0] in SHELL_META:
        return ""
    try:
        words = shlex.split(cmd)
    except ValueError:
        return ""
    program = next((w for w in words if not _ENV_WORD.match(w)), "")
    if not program or any(ch in SHELL_META for ch in program):
        return ""
    if program in SHELL_WORDS or "/" in program:
        return ""
    return "" if shutil.which(program, path=path) else program


def looks_like_not_found(stderr: str) -> bool:
    low = stderr.lower()
    return "not found" in low or "no such file or directory" in low or "permission denied" in low


@dataclass(frozen=True)
class Declared:
    """A command line an operator wrote in configuration, and where.

    Build one only from a config reader's value; ``source`` names that place (a gate id, a
    config key) so a result can say whose line it ran.
    """

    line: str
    source: str

    def __post_init__(self) -> None:
        if not isinstance(self.line, str) or not self.source.strip():
            raise ValueError("a Declared command needs its line and the config it came from")

    def __str__(self) -> str:
        return self.line


@dataclass
class CommandRun:
    """How one configured command ended. ``status`` is "ran" (``code`` is its exit status)
    or "unavailable" (``kind`` and ``reason`` say why nothing was checked)."""

    status: str
    command: str = ""
    kind: str = ""
    reason: str = ""
    code: int | None = None
    out: str = ""
    err: str = ""
    elapsed_s: float = 0.0
    #: The program that is not installed, for kind MISSING.
    missing: str = ""
    #: Of the whole raw output (``output_bytes`` is a count of characters),
    #: before any clipping or redaction.
    digest: str = ""
    output_bytes: int = 0
    truncated: bool = False
    redactions: dict[str, int] = field(default_factory=dict)
    #: Seconds spent waiting for an admission slot.
    waited_s: float = 0.0

    @property
    def ran(self) -> bool:
        return self.status == "ran"

    @property
    def output(self) -> str:
        return self.out + self.err


@dataclass
class Started:
    """`CommandRunner.start`'s answer: the live ``proc``, or why there is none (``unavailable``,
    a `CommandRun` of status "unavailable")."""

    proc: Any
    unavailable: CommandRun | None = None


def _unavailable(command: str, kind: str, reason: str, **more: Any) -> CommandRun:
    return CommandRun("unavailable", command, kind, reason, **more)


class CommandRunner:
    """Runs `Declared` lines. ``slots``/``slot_limit``/``slot_wait_s`` admit at most
    ``slot_limit`` at once across processes; ``max_output`` clips what is kept;
    ``redactor`` masks it."""

    def __init__(
        self,
        *,
        slots: Slots | None = None,
        slot_limit: int = 1,
        slot_wait_s: float | None = None,
        max_output: int | None = None,
        redactor: Redactor | None = None,
    ) -> None:
        self.slots = slots
        self.slot_limit = slot_limit
        self.slot_wait_s = slot_wait_s
        self.max_output = max_output
        self.redactor = redactor

    def run(
        self,
        declared: Declared,
        *,
        cwd: Path | str | None = None,
        env: Mapping[str, str] | None = None,
        timeout_s: float | None,
        on_tick: Callable[[], None] | None = None,
        tick_s: float = 0,
        check_installed: bool = True,
        merge_stderr: bool = False,
        stdin_text: str | None = None,
    ) -> CommandRun:
        """Run ``declared``; with ``merge_stderr`` its stderr is folded into ``out``, with
        ``stdin_text`` it is fed that text on stdin (the embedder's JSON request). Never raises for the command's own trouble.

        An ``on_tick`` that fails is the caller's trouble: the process group is killed and
        the run is unavailable with the error, as a command that could not run.
        """
        if not isinstance(declared, Declared):
            raise TypeError("CommandRunner.run takes a Declared operator command, not a string")
        run = self._admitted(
            declared.line,
            cwd,
            env,
            timeout_s,
            on_tick,
            tick_s,
            check_installed,
            merge_stderr,
            stdin_text,
        )
        return self._clean(run)

    def start(
        self,
        declared: Declared,
        *,
        env: Mapping[str, str] | None = None,
        cwd: Path | str | None = None,
        stdin: Any = P.DEVNULL,
        stdout: Any = P.PIPE,
        stderr: Any = P.PIPE,
        check_installed: bool = True,
    ) -> Started:
        """Start ``declared`` and hand the live process back, for a command that streams or
        outlives the call (a command reviewer fed on stdin, a reviewer's server).

        What is shared is the part that does not need the process to finish: the same
        declared-only rule, the same installed-check (against the run's own PATH) and a
        session of its own, so `P.kill_group` ends everything it started. Timeout, Slots
        admission, output clipping and redaction stay with the caller, because they act on a
        result and a stream has none until the caller has read it.
        """
        if not isinstance(declared, Declared):
            raise TypeError("CommandRunner.start takes a Declared operator command, not a string")
        line = declared.line
        if check_installed and (
            missing := executable_missing(
                line, None if env is None else env.get("PATH", os.defpath)
            )
        ):
            missing_run = _unavailable(
                line, MISSING, f"{missing!r} is not installed", missing=missing
            )
            return Started(None, self._clean(missing_run))
        try:
            proc = P.spawn_shell(
                line,
                env=None if env is None else dict(env),
                cwd=None if cwd is None else str(cwd),
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
            )
        except (OSError, ValueError) as exc:
            return Started(
                None, self._clean(_unavailable(line, COULD_NOT_RUN, f"could not execute: {exc}"))
            )
        return Started(proc, None)

    def _admitted(
        self,
        line: str,
        cwd: Path | str | None,
        env: Mapping[str, str] | None,
        timeout_s: float | None,
        on_tick: Callable[[], None] | None,
        tick_s: float,
        check_installed: bool,
        merge_stderr: bool = False,
        stdin_text: str | None = None,
    ) -> CommandRun:
        if check_installed and (
            missing := executable_missing(
                line, None if env is None else env.get("PATH", os.defpath)
            )
        ):
            return _unavailable(line, MISSING, f"{missing!r} is not installed", missing=missing)
        if self.slots is None:
            return self._run(
                line, cwd, env, timeout_s, on_tick, tick_s, 0.0, merge_stderr, stdin_text
            )
        try:
            with self.slots.hold(self.slot_limit, self.slot_wait_s) as slot:
                return self._run(
                    line,
                    cwd,
                    env,
                    timeout_s,
                    on_tick,
                    tick_s,
                    slot.waited_s,
                    merge_stderr,
                    stdin_text,
                )
        except SlotsTimeout as exc:
            return _unavailable(line, BUSY, str(exc))

    def _run(
        self,
        line: str,
        cwd: Path | str | None,
        env: Mapping[str, str] | None,
        timeout_s: float | None,
        on_tick: Callable[[], None] | None,
        tick_s: float,
        waited_s: float,
        merge_stderr: bool = False,
        stdin_text: str | None = None,
    ) -> CommandRun:
        ticking = on_tick is not None and tick_s > 0
        try:
            p = P.run_shell(
                line,
                timeout_s=timeout_s,
                cwd=None if cwd is None else str(cwd),
                env=None if env is None else dict(env),
                on_tick=on_tick if ticking else None,
                tick_s=tick_s if ticking else 0,
                merge_stderr=merge_stderr,
                stdin_text=stdin_text,
            )
        except (OSError, ValueError) as exc:  # the keep-alive tick failed; the command is killed
            return _unavailable(line, COULD_NOT_RUN, f"could not execute: {exc}")
        if p.timed_out:
            return self._finish(
                _unavailable(line, TIMEOUT, f"timed out after {timeout_s}s", elapsed_s=p.elapsed_s),
                p,
            )
        if p.could_not_run:
            return _unavailable(line, COULD_NOT_RUN, f"could not execute: {p.err}")
        run = CommandRun("ran", line, code=p.code, elapsed_s=p.elapsed_s, waited_s=waited_s)
        # POSIX reserves 127 for "command not found" and 126 for "found but not
        # executable"; the shell says so on stderr. A suite that chose to exit 127 does not.
        if p.code in (126, 127) and looks_like_not_found(p.err):
            run.status, run.kind = "unavailable", NOT_FOUND
            run.reason = (
                f"shell reported exit {p.code} (command not found / not executable): "
                f"{p.err.strip()[:200]}"
            )
        return self._finish(run, p)

    def _finish(self, run: CommandRun, p: P.ShellResult) -> CommandRun:
        raw = p.out + p.err
        run.out, run.err = p.out, p.err
        run.digest = content_digest(raw, "blake2b", size=8, errors="replace")
        run.output_bytes = len(raw)
        return run

    def _clean(self, run: CommandRun) -> CommandRun:
        """Mask and clip what a result holds, every path alike: the output, and the reason
        (which can quote the command's stderr or a path from the OS error)."""
        if self.redactor is not None:
            for name in ("out", "err"):
                red = self.redactor.text(getattr(run, name))
                setattr(run, name, red.text)
                for kind, n in red.counts.items():
                    run.redactions[kind] = run.redactions.get(kind, 0) + n
            run.reason = self.redactor.text(
                run.reason
            ).text  # masked, not counted: counts describe the output
        if self.max_output is not None:
            marker = f"[... clipped to the last {self.max_output} characters]\n"
            for name in ("out", "err"):
                text = getattr(run, name)
                if len(text) > self.max_output:
                    setattr(run, name, clip(text, self.max_output, marker=marker, side="tail"))
                    run.truncated = True
        return run

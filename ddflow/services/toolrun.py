"""Wires the `ExternalTool` port to the one command runner (ADP-tools).

`ddflow.adapters.tools` starts no process; this is where production gives it one. The runner it
is handed is `cmdrunner.CommandRunner`, so a tool that is not installed, a timeout, a process
that could not start, the shell's own "not found" and a full admission queue all arrive as
`ran=False` with the reason -- the same mapping every other command gets, not a second one.

The argv is quoted into one `Declared` line (`shlex.join`), so an argument with spaces or shell
syntax stays one token. The line is ddflow's own (a row of `adapters/tools.toml` plus the
caller's arguments), and its `source` says so.
"""

from __future__ import annotations

import shlex
from collections.abc import Sequence
from pathlib import Path

from ..adapters import tools as T
from ..adapters.tool_types import RunResult, ToolRunner
from ..config import Config
from .cmdrunner import CommandRunner, Declared

#: The `Declared.source` of a tool line: where the argv came from.
SOURCE = "ddflow/adapters/tools.toml"


def command_tool_runner(runner: CommandRunner | None = None, *, source: str = SOURCE) -> ToolRunner:
    """A `ToolRunner` over ``runner`` (default: a plain `CommandRunner`)."""
    run = runner or CommandRunner()

    def call(argv: Sequence[str], *, timeout_s: float, cwd: str | None = None) -> RunResult:
        done = run.run(Declared(shlex.join(argv), source), cwd=cwd, timeout_s=timeout_s)
        if not done.ran:
            return RunResult(False, None, done.out, done.err, done.kind, done.reason)
        return RunResult(True, done.code, done.out, done.err)

    return call


def external_tool(
    tool_id: str,
    repo: Path,
    runner: CommandRunner | None = None,
    *,
    pin: str | None = None,
) -> T.TableTool:
    """The production adapter for ``tool_id``: probe and run through `cmdrunner`, with the
    timeouts of `[companions] tool_probe_timeout_s` and `tool_run_timeout_s` read from ``repo``'s
    configuration."""
    cfg = Config.load(repo).companions
    return T.tool(
        tool_id,
        command_tool_runner(runner),
        pin=pin,
        probe_timeout_s=cfg.tool_probe_timeout_s,
        run_timeout_s=cfg.tool_run_timeout_s,
    )

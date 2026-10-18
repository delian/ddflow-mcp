"""``cmd:<name>``: the output of a command the operator declared, as text.

The name is looked up in the operator's configuration (`Context.commands`); a section can
never carry a command line of its own. The command runs through the one `CommandRunner`
(timeout, installed-check, output cap, redaction); a binary that is not installed, a timeout or
a non-zero exit is `SourceUnavailable`, never an empty section.
"""

from __future__ import annotations

from typing import Any

from ..cmdrunner import SHELL_META, CommandRunner, Declared
from .sources import Context, SourceError, SourceKind, SourceUnavailable, register

_NAME_OK = set("abcdefghijklmnopqrstuvwxyz0123456789_-")


def _check(arg: str) -> str:
    if not arg or set(arg) - _NAME_OK:
        raise SourceError(f"source 'cmd' names a configured command (lowercase, - _): {arg!r}")
    return arg


def _data(arg: str, ctx: Context) -> dict[str, Any]:
    line = ctx.commands.get(arg)
    if not line:
        raise SourceUnavailable(
            f"no command {arg!r} is declared; configure it where the project declares doc commands"
        )
    if SHELL_META & set(line):
        raise SourceError(
            f"command {arg!r} uses shell syntax; a doc command is one program and its arguments"
        )
    run = CommandRunner(max_output=ctx.max_bytes).run(
        Declared(line, f"doc command {arg}"), cwd=ctx.repo, timeout_s=ctx.command_timeout_s
    )
    redact = ctx.redactor or (lambda t: t)
    if not run.ran:
        raise SourceUnavailable(f"command {arg!r} did not run: {redact(str(run.reason))}")
    if run.code != 0:
        raise SourceUnavailable(
            f"command {arg!r} exited {run.code}: {redact(run.err.strip())[:200]}"
        )
    text = redact(run.out)
    return {"text": text, "command": arg}


register(SourceKind("cmd", _check, _data))

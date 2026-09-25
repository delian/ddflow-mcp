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

import subprocess
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

"""An identity declared with `ddflow_identify`, visible to the same harness's shell.

`ddflow_identify` names the agent on ONE MCP connection. The agent's shell commands --
`ddflow claim`, `ddflow heartbeat`, the git commit hook -- are other processes, and they
derived their own name from the working tree. One agent's claim and its heartbeat then
landed under two identities: the MCP heartbeat answered "no lease held", and the commit
hook refused the agent's own files (Bfad021e8d9).

Both kinds of process descend from the same harness: the MCP server is its child (via a
launcher such as `uv` at most), the shell a descendant. So the declaration is recorded
against the harness -- the server's parent, and through any launcher up to the first
process that is not one -- keyed by pid AND start time, so a reused pid never inherits
it. A CLI run that names no identity (`--agent`, `DDFLOW_AGENT`) takes the record of its
nearest ancestor that has one.

Records live in the primary checkout's `.git` directory: per repository, never committed.
Linux only (it reads /proc); elsewhere nothing is recorded and nothing is found.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

#: Under the primary checkout's `.git`.
DIR = "ddflow-identity"

#: Processes that only start another: the harness is above them, not them.
LAUNCHERS = frozenset({"uv", "uvx", "pipx", "npx", "env", "sh", "dash", "bash", "zsh"})

_NAME = re.compile(r"[A-Za-z0-9._-]{1,64}")
_MAX_DEPTH = 64


def _stat(pid: int) -> tuple[str, int, str] | None:
    """(comm, ppid, start time) of `pid`, or None when it cannot be read."""
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    # comm is in parentheses and may itself contain spaces or ')'.
    comm, _, rest = raw[raw.find("(") + 1 :].rpartition(")")
    fields = rest.split()
    try:
        return comm, int(fields[1]), fields[19]
    except (IndexError, ValueError):
        return None


def _dir(repo: Path | str) -> Path | None:
    git = Path(repo) / ".git"
    return git / DIR if git.is_dir() else None


def _harness(pid: int) -> list[str]:
    """Record keys for `pid` and, while it is a launcher, its ancestors (at most 4)."""
    keys: list[str] = []
    for _ in range(4):
        st = _stat(pid) if pid > 1 else None
        if st is None:
            break
        comm, ppid, start = st
        keys.append(f"{pid}-{start}")
        if comm not in LAUNCHERS:
            break
        pid = ppid
    return keys


def declare(repo: Path | str, agent: str) -> None:
    """Record `agent` for this process's harness; "" withdraws it. Never raises."""
    d = _dir(repo)
    if d is None:
        return
    try:
        for key in _harness(os.getppid()):
            f = d / key
            if agent:
                d.mkdir(exist_ok=True)
                f.write_text(agent + "\n")
            else:
                f.unlink(missing_ok=True)
    except OSError:
        pass


def declared(repo: Path | str) -> str:
    """The identity a harness above this process declared for `repo`, or ""."""
    d = _dir(repo)
    if d is None or not d.is_dir():
        return ""
    pid = os.getppid()
    for _ in range(_MAX_DEPTH):
        st = _stat(pid) if pid > 1 else None
        if st is None:
            return ""
        _comm, ppid, start = st
        try:
            name = (d / f"{pid}-{start}").read_text().strip()
        except OSError:
            name = ""
        if _NAME.fullmatch(name):
            return name
        pid = ppid
    return ""

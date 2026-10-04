"""An identity declared with `ddflow_identify`, visible to the same harness's shell.

`ddflow_identify` names the agent on ONE MCP connection. The agent's shell commands --
`ddflow claim`, `ddflow heartbeat`, the git commit hook -- are other processes, and they
derived their own name from the working tree. One agent's claim and its heartbeat then
landed under two identities: the MCP heartbeat answered "no lease held", and the commit
hook refused the agent's own files (Bfad021e8d9).

Both kinds of process descend from the same harness: the MCP server is its child (via a
launcher such as `uv`, or a `sh -c` wrapper), the shell a descendant. So the
declaration is recorded against the harness -- the server's parent, and through any
launcher up to the first process that is not one -- keyed by pid AND start time, so a
reused pid never inherits it. A CLI run that names no identity (`--agent`,
`DDFLOW_AGENT`) takes the record of its nearest ancestor that has one; a server
restarted under the same harness starts with that harness's own record (`own`), so it
and the shell stay one agent.

Records live in the repository's common `.git` directory: per repository, never
committed. Linux only (it reads /proc); elsewhere nothing is recorded and nothing is
found, and `declare` says so.

Every shell below the harness takes the name -- a subagent's too, unless it names its
own (`--agent`, `DDFLOW_AGENT`). That is the same rule as its MCP calls on the shared
connection, which carry the parent's identity unless they pass `as_agent`.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

#: Under the primary checkout's `.git`.
DIR = "ddflow-identity"

#: Processes that only start another: the harness is above them, not them.
LAUNCHERS = frozenset({"uv", "uvx", "pipx", "npx", "env"})

#: Shells are launchers only when they run a command or a script (`sh -c ...`,
#: `bash start.sh`). An INTERACTIVE one may be the harness, or the terminal above it,
#: and climbing past it would hand the name to every command typed there.
SHELLS = frozenset({"sh", "dash", "bash", "zsh"})

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
    """`<common git dir>/ddflow-identity`, from a primary checkout or a linked worktree."""
    git = Path(repo) / ".git"
    if git.is_dir():
        return git / DIR
    # A linked worktree: `.git` is a file naming its gitdir, whose `commondir` names the
    # shared one. Read, not asked of git: this runs on every CLI call in a worktree.
    try:
        line = git.read_text().strip()
        if not line.startswith("gitdir:"):
            return None
        gitdir = (Path(repo) / line[len("gitdir:") :].strip()).resolve()
    except OSError:
        return None
    try:
        common = (gitdir / (gitdir / "commondir").read_text().strip()).resolve()
    except OSError:  # no commondir: a separate git dir or a submodule, which is its own
        common = gitdir
    return common / DIR


def _gone(key: str) -> bool:
    """Has the process a record names DEFINITELY exited? Unsure is not gone."""
    pid, _, start = key.partition("-")
    if not pid.isdigit():
        return False
    if not Path(f"/proc/{pid}").exists():
        return Path("/proc/self").exists()  # no /proc at all says nothing
    st = _stat(int(pid))
    return st is not None and st[2] != start


def _wrapper_shell(pid: int) -> bool:
    """A shell running a command or script: anything but option flags after argv[0]."""
    try:
        argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")[1:]
    except OSError:
        return False
    return any(a == b"-c" or (a and not a.startswith(b"-")) for a in argv)


def _harness(pid: int) -> list[str]:
    """Record keys for `pid` and, while it is a launcher, its ancestors (at most 4)."""
    keys: list[str] = []
    for _ in range(4):
        st = _stat(pid) if pid > 1 else None
        if st is None:
            break
        comm, ppid, start = st
        keys.append(f"{pid}-{start}")
        if comm not in LAUNCHERS and not (comm in SHELLS and _wrapper_shell(pid)):
            break
        pid = ppid
    return keys


def declare(repo: Path | str, agent: str) -> str:
    """Record `agent` for this process's harness; "" withdraws it. Never raises.

    Returns "" when it did, else why not -- which `ddflow_identify` reports, because a
    declaration the shell will not see is the original split, silently back."""
    if agent and not _NAME.fullmatch(agent):
        return (
            f"not recorded for shell commands: {agent!r} is not a usable agent name, "
            "so they keep any name declared before"
        )
    d = _dir(repo)
    keys = _harness(os.getppid())
    if d is None or not keys:
        return "not recorded for shell commands (needs a git repository and /proc)"
    try:
        if d.is_dir():
            for old in d.iterdir():  # harnesses that have exited
                if _gone(old.name):
                    old.unlink(missing_ok=True)
        for key in keys:
            f = d / key
            if agent:
                d.mkdir(exist_ok=True)
                f.write_text(agent + "\n")
            else:
                f.unlink(missing_ok=True)
    except OSError as exc:
        return f"not recorded for shell commands: {exc}"
    return ""


def own(repo: Path | str) -> str:
    """The record of THIS process's own harness (see `_harness`), or "": for a server
    restarting under it. Never an outer harness's: a nested agent CLI started from
    another agent's shell must not inherit that agent's name."""
    d = _dir(repo)
    if d is None or not d.is_dir():
        return ""
    for key in _harness(os.getppid()):
        try:
            name = (d / key).read_text().strip()
        except OSError:
            continue
        if _NAME.fullmatch(name):
            return name
    return ""


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

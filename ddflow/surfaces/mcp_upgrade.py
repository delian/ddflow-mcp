"""The MCP server after ddflow was upgraded under it (bug B3f7d0f7bf3).

A stdio server is a long-lived process: one started before an upgrade keeps the code it
imported, and then refuses writes on version skew -- against a project the new code (the shell,
a sibling session) already moved on. `[mcp].on_upgrade` says what it does about that:

* ``notify`` (default): a skew refusal that staleness explains is answered with the one fix
  (reconnect the server) instead of "upgrade ddflow" -- the user already did -- and every other
  call carries a one-line note, once. Reads keep working throughout.
* ``reexec``: after answering the current call, once nothing else is in flight or waiting on
  stdin, the process replaces itself with the installed code on the same stdio, carrying the
  connection's protocol, client and declared identity. Only when the installed package is newer
  than the running code and this process was not itself started by a re-exec at this version
  (else the exec would change nothing, and loop).
* ``off``: neither.

Everything here swallows its failures: a courtesy never turns a call into an error.
"""

from __future__ import annotations

import json
import os
import select
import sys
import time
from pathlib import Path
from typing import Any

from ..config import Config
from ..core.events import SkewRefused
from ..core.version import running as running_version
from .tools._common import _api

ON_UPGRADE = ("notify", "reexec", "off")
#: Carries the connection from the old process to the new one across the exec.
HANDOFF_ENV = "DDFLOW_MCP_HANDOFF"
#: How often a running server looks at the installed package and the log's stamps.
CHECK_EVERY_S = 60.0

RESTART_STEPS = (
    "To fix it, restart the MCP server: reconnect it in your client (Claude Code: /mcp, pick "
    "ddflow, Reconnect; otherwise restart the client or its MCP server), then retry. Reads "
    "still work, and the shell `ddflow` already runs the new code. Do not pass "
    "allow_older_version: nothing is wrong with the project."
)


def policy(repo: Path) -> str:
    """`[mcp].on_upgrade`, read per call; an unknown value or unreadable config is notify."""
    try:
        value = Config.load(repo).mcp.on_upgrade
    except Exception:
        return "notify"
    return value if value in ON_UPGRADE else "notify"


def facts(srv) -> dict[str, Any] | None:
    """`stale_server_facts` for this connection, None when current or unreadable."""
    try:
        return _api().stale_server_facts(srv.repo, agent=srv.agent)
    except Exception:
        return None


def note(f: dict[str, Any]) -> str:
    return (
        f"note: this ddflow MCP server is running {f['running']}, but {f['where']} is at "
        f"{f['newest']}: restart the server (reconnect the MCP server in your client) to pick "
        "up the upgrade."
    )


def can_reexec(f: dict[str, Any]) -> bool:
    """Would a re-exec change anything, and is it not the one this process came from?"""
    return bool(f.get("reexec")) and _handoff().get("from") != f["running"]


def refusal(srv, exc: Exception) -> dict[str, Any] | None:
    """The tool result for a skew refusal that staleness explains, else None (show it as it is).

    Unthrottled: it runs only on a refusal, which is rare and already slow."""
    if not isinstance(exc, SkewRefused):
        return None
    mode = policy(srv.repo)
    if mode == "off":
        return None
    f = facts(srv)
    if f is None or not f["reexec"]:
        return None  # nothing newer is installed: a restart would not help, the refusal is true
    again = mode == "reexec" and can_reexec(f)
    if again:
        srv.reexec_pending = True
        how = "This server is restarting onto the installed code; retry the call in a moment."
    else:
        how = RESTART_STEPS
    body = (
        f"REFUSED: this project's log has been worked on by ddflow {f['newest']} "
        f"({f['where']}), and this MCP server is still running {f['running']}: the server "
        f"is stale, not the install (ddflow >= {f['newest']} is needed, and is already "
        f"installed). Writing from here could drop what the newer one recorded. {how}"
    )
    meta = {"exit": 3, "restart_required": True, "running": f["running"], "newest": f["newest"]}
    return {"content": [{"type": "text", "text": body}], "isError": False, "_meta": meta}


def footer(srv) -> str:
    """The once-a-minute check after a successful call: ``notify`` returns the note (once),
    ``reexec`` flags the server to replace itself (and says nothing), ``off`` does nothing."""
    if srv._stale_said or srv.reexec_pending:
        return ""
    now = time.monotonic()  # not the wall clock: a clock stepped back must not mute the note
    if now - srv._stale_checked_at < CHECK_EVERY_S:
        return ""
    srv._stale_checked_at = now
    try:
        mode = policy(srv.repo)
        f = facts(srv) if mode != "off" else None
        if f is None or not f["reexec"]:
            return ""  # nothing newer is installed: "restart" would be a dead end
        if mode == "reexec" and can_reexec(f):
            srv.reexec_pending = True
            return ""
        srv._stale_said = True
        return note(f)
    except Exception:
        return ""


# -- the re-exec ---------------------------------------------------------------------------


def _handoff() -> dict[str, Any]:
    try:
        data = json.loads(os.environ.get(HANDOFF_ENV, ""))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def adopt(srv) -> None:
    """In a process started by a re-exec: restore the connection the old one carried."""
    data = _handoff()
    if not data:
        return
    try:
        srv.protocol = data.get("protocol") or srv.protocol
        srv.client_info = dict(data.get("client_info") or {})
        srv.agent = data.get("agent") or srv.agent
    except Exception:
        pass


def called_from() -> Path | None:
    value = _handoff().get("called_from")
    return Path(value) if value else None


def execute(srv) -> None:
    """Replace this process with the installed code, same stdio. Returns only when the exec
    could not happen (the server then carries on, and says so on stderr)."""
    state = {
        "from": running_version(),
        "protocol": srv.protocol,
        "client_info": srv.client_info,
        "agent": srv.agent,
        "called_from": str(srv.called_from) if srv.called_from else "",
    }
    env = dict(os.environ)
    env[HANDOFF_ENV] = json.dumps(state)
    argv = [sys.executable, "-m", "ddflow.surfaces.mcp", "--repo", str(srv.repo)]
    try:
        sys.stdout.flush()
        sys.stderr.flush()
        os.execve(sys.executable, argv, env)
    except OSError as exc:
        srv.reexec_pending = False
        print(f"ddflow-mcp: re-exec failed ({exc}); staying on the running code", file=sys.stderr)


class StdinLines:
    """Lines from a file descriptor, read unbuffered beyond the line in hand, so that what the
    client has sent but this process has not read stays in the pipe across an exec (a buffered
    text stream's read-ahead would be lost). ``idle`` says nothing is waiting."""

    def __init__(self, fd: int = 0) -> None:
        self.fd = fd
        self.buf = bytearray()

    def idle(self) -> bool:
        if self.buf:
            return False
        try:
            return not select.select([self.fd], [], [], 0)[0]
        except (OSError, ValueError):
            return False

    def __iter__(self):
        while True:
            at = self.buf.find(b"\n")
            if at < 0:
                chunk = os.read(self.fd, 65536)
                if not chunk:
                    if self.buf:
                        line, self.buf = bytes(self.buf), bytearray()
                        yield line.decode("utf-8", "replace")
                    return
                self.buf += chunk
                continue
            line = bytes(self.buf[: at + 1])
            del self.buf[: at + 1]
            yield line.decode("utf-8", "replace")


def stdio_lines():
    """The process's stdin as `StdinLines` when it is a real descriptor, else `sys.stdin`
    itself (a test's replacement stream has none, and never re-execs)."""
    try:
        return StdinLines(sys.stdin.fileno())
    except (OSError, ValueError, AttributeError):
        return sys.stdin

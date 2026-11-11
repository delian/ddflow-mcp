"""The managed MCP server lifecycle: the `restart` (soft) and `kill` (hard) commands.

A ddflow MCP server is a small surface detached from the major code, so a SOFT upgrade can
swap it without an agent restart: `restart` reloads the code and re-binds the tools while
keeping the connection (`mcp_upgrade` re-execs the process onto the same stdio). `kill` is
the HARD upgrade: the server ends its process, and the agent — its client, or the container
supervisor that spawned it — respawns it fresh.

When the server is NOT local — its transport is not a stdio it owns, so it cannot re-exec
itself in place — `restart` and `kill` both resolve to a REBOOT: the server process/container
ends so its supervisor restarts it onto the installed code.

The verbs answer with a plan (`hosting`, `action`) that both surfaces show; over MCP the
engine applies it (`arm`) once the answer is out, so a restart re-execs when the connection
is quiet and a kill ends the process only after the reply was written.
"""

from __future__ import annotations

import os
import sys

from ..core import outcome as O

#: The environment that declares how this server is hosted (`local` | `remote`).
HOSTING_ENV = "DDFLOW_MCP_HOSTING"
#: The two hostings; `remote` also stands for a container and any non-local supervisor.
HOSTINGS = ("local", "remote")


def hosting() -> str:
    """Whether this server can replace itself in place (`local`) or must reboot (`remote`).

    `DDFLOW_MCP_HOSTING` declares it outright (a remote/container deploy sets `remote`).
    Otherwise it is detected: a local stdio server owns a real descriptor and re-execs onto
    the same one (`mcp_upgrade.StdinLines`); a server whose stdin is not one is remote.
    """
    declared = os.environ.get(HOSTING_ENV, "").strip().lower()
    if declared in HOSTINGS:
        return declared
    from . import mcp_upgrade as _upgrade  # deferred: importing it imports the config layer

    try:
        if isinstance(_upgrade.stdio_lines(), _upgrade.StdinLines):
            return "local"
    except Exception:
        pass
    return "remote"


def plan(verb: str) -> dict[str, str]:
    """``(hosting, action)`` a `restart` or `kill` resolves to on this server."""
    home = hosting()
    if verb == "restart":
        action = "reexec" if home == "local" else "reboot"
    elif verb == "kill":
        action = "exit" if home == "local" else "reboot"
    else:
        raise ValueError(f"unknown lifecycle verb {verb!r}")
    return {"hosting": home, "action": action}


def _message(verb: str, action: str) -> str:
    messages = {
        ("restart", "reexec"): (
            "This MCP server is restarting onto the installed code and re-binding its tools "
            "on the same connection; retry the call in a moment."
        ),
        ("restart", "reboot"): (
            "This server runs remotely (its transport is not a local stdio it owns), so "
            "`restart` resolves to a reboot: the server process/container ends and its "
            "supervisor respawns it onto the installed code."
        ),
        ("kill", "exit"): (
            "Ending this MCP server and its processes; your client respawns it fresh on the "
            "next call."
        ),
        ("kill", "reboot"): (
            "This server runs remotely, so `kill` resolves to a reboot: the server "
            "process/container ends and its supervisor respawns it."
        ),
    }
    return messages[(verb, action)]


def outcome(verb: str) -> O.Outcome:
    """What a `restart` or `kill` resolves to, as the answer both surfaces show."""
    p = plan(verb)
    return O.ok(
        "mcp_lifecycle",
        verb=verb,
        hosting=p["hosting"],
        action=p["action"],
        message=_message(verb, p["action"]),
    )


def arm(srv, verb: str) -> None:
    """Apply the verb to the running connection AFTER its answer is out.

    A restart re-execs in place when it can (`srv.reexec_pending`); a kill — and any verb on
    a remote server — ends the process (`srv.kill_pending`) so the client or supervisor
    respawns it. The engine calls this from `Server._invoke`, which runs inside the tool call
    and before the answer is written, so the process-level effect is deferred until the loop
    is quiet (`surfaces.mcp._serve.after`).
    """
    if verb == "restart" and plan(verb)["action"] == "reexec":
        srv.reexec_pending = True
    else:
        srv.kill_pending = True


def exit_process(exit_code: int = 1) -> None:
    """End this server process after its answer is out (a kill, or a remote reboot).

    Never returns: `os._exit` skips interpreter shutdown, so buffered teardown (which would
    lose the already-flushed reply, or a lingering signal handler) cannot defer the exit.
    """
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    finally:
        os._exit(exit_code)

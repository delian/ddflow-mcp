"""The `restart` and `kill` server-lifecycle commands, declared once.

A soft `restart` reloads the MCP server's code and re-binds its tools while keeping the
connection; a hard `kill` ends the server so its client (or container supervisor) respawns
it fresh. When the server is not local, both resolve to a reboot of the server
process/container. The decision (``hosting`` and ``action``) is answered by both surfaces
and applied over MCP by the engine (`mcp_lifecycle.arm`) once the answer is out; the CLI
prints the same decision, since a shell process is not the server it would re-exec.
"""

from __future__ import annotations

from .. import mcp_lifecycle as _LC
from ..registry import Command


def _restart(repo, a, agent):
    return _LC.outcome("restart")


def _kill(repo, a, agent):
    return _LC.outcome("kill")


def _text(out, a) -> str:
    return out.data["message"]


COMMANDS: tuple[Command, ...] = (
    Command(
        path=("restart",),
        summary="soft upgrade: reload this MCP server's code and re-bind its tools, keeping the connection",
        tool="ddflow_restart",
        lifecycle="restart",
        family="ddflow_admin",
        action="restart",
        tier="all",
        help_group="When something is wrong",
        description=(
            "Reload this ddflow MCP server's code and re-bind its tools WITHOUT an agent "
            "restart: over MCP the server replaces itself on the same connection once the "
            "call is answered, so the next call runs the installed code. When the server is "
            "not local (a container or remote supervisor owns its transport) this resolves to "
            "a reboot of the server process/container. Answers `hosting` (local|remote) and "
            "`action` (reexec|reboot|exit)."
        ),
        call=_restart,
        payload=("verb", "hosting", "action", "message"),
        render=_text,
    ),
    Command(
        path=("kill",),
        summary="hard upgrade: end this MCP server's process so your client respawns it fresh",
        tool="ddflow_kill",
        lifecycle="kill",
        family="ddflow_admin",
        action="kill",
        tier="all",
        help_group="When something is wrong",
        description=(
            "Hard end of this ddflow MCP server: the process dies after the call is answered "
            "and your client respawns it fresh on the next call. When the server is not local "
            "(a container or remote supervisor owns its transport) this resolves to a reboot "
            "of the server process/container, same as `restart`. Answers `hosting` "
            "(local|remote) and `action` (exit|reboot)."
        ),
        call=_kill,
        payload=("verb", "hosting", "action", "message"),
        render=_text,
    ),
)

"""The `Proc` port: run subprocesses with stdin detached from the MCP stdio session.

Home of the port, and (as the family moves in, ADP-migrate-homes) of its default adapter.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .ports import port


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

"""The `Transport` port: where and how a lane's process runs, and how its files come back.

One contract, one adapter per EXECUTOR (local subprocess, tmux, ssh, container, dsh, and a
git-bundle remote). The verbs are the ones `LaneTransport` grew on `SW-remote-bundle`
(`ddflow.services.swarm.remote`) and the local lane launcher: **start** a lane's argv list,
**signal** it, **observe** liveness and exit, **copy** files in and out (the lane's own output
is its log file, read back with `copy_out`), and **cleanup**. The default adapter is
`LocalTransport` (`ddflow.adapters.transport_local`), registered under ``local``; the other
executors register the same way, and remote trust/consent stay in the swarm decisions, not here.

Rules every adapter keeps (from the remote bundle): a lane's argv is a list, never a shell
line; a host/executor that cannot be reached is ``unavailable``, never ``failed`` or ok; a
lane's own directory is the only place an executor writes; a file read back is cut at its cap
with nothing left behind on failure.

Multi-adapter family: `name` is the registry key, `target` says where one instance runs.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from .lanes import LaneStatus, Result
from .ports import port
from .registry import Registry


@port
class Transport(Protocol):
    """Where a lane's process runs.

    A multi-adapter family (local, tmux, ssh, container, dsh, remote bundle), keyed by
    ``name`` in `TRANSPORTS`; ``target`` is where one instance runs (``"local"`` for the
    default, a host name for ssh). `name` is data, so this port is not `runtime_checkable`;
    a LaneTransport a host that cannot be reached answers ``unavailable``, never ``failed``.

    Home: `ddflow.adapters.transport` (default adapter `LocalTransport` in
    `ddflow.adapters.transport_local`).
    """

    name: str
    target: str

    def start(self, lane: str, argv: Sequence[str]) -> Result:
        """Launch ``argv`` in the lane's own directory; refuse a lane already running."""

    def signal(self, lane: str, sig: str = "TERM") -> Result:
        """Send one signal to the lane's whole process group."""

    def observe(self, lane: str) -> LaneStatus | Result:
        """The lane's liveness and exit (a `LaneStatus`), or a failed Result when absent."""

    def copy_in(self, lane: str, local: Path, name: str) -> Result:
        """Copy the file ``local`` into the lane's directory as ``name``."""

    def copy_out(self, lane: str, name: str, local: Path, max_bytes: int) -> Result:
        """Copy the lane's file ``name`` to ``local``, cut at ``max_bytes``; nothing stays at
        ``local`` unless the whole file came (the lane's output is its ``lane.log``)."""

    def cleanup(self, lane: str) -> Result:
        """Kill the lane's process group (when it has one) and remove its directory."""


#: The transport adapters, keyed by their `name`. `LocalTransport` is the default; tmux, ssh,
#: container and dsh register as their own modules, never an edit to a shared file.
TRANSPORTS: Registry[type[Transport]] = Registry("lane transport")
TRANSPORTS.lazy("local", "ddflow.adapters.transport_local:LocalTransport")

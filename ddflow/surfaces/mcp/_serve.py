"""The stdio loop and the console-script entry point.

`serve` reads newline-delimited JSON-RPC until EOF and `main` is the `ddflow-mcp` /
`python -m ddflow.surfaces.mcp` entry point. Split out of the old `surfaces/mcp.py` engine.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from ...adapters.mcp import StdioServer
from ...api import cap_blas_threads
from ...infra import proc as PROC
from ...infra.worktree import repo_root
from .. import mcp_lifecycle as _lifecycle
from .. import mcp_upgrade as _upgrade
from ..mcp_protocol import SERVER_INFO
from ..mcp_protocol import modern_check as _modern_check
from ..mcp_protocol import modernize as _modernize
from ._offload import _offload, _offloaded
from ._server import Server
from ._snapfast import write_after as _write_snapshot


def serve(
    repo: Path,
    stdin=None,
    stdout=None,
    *,
    called_from: Path | None = None,
    offload: bool | None = None,
) -> None:
    """Newline-delimited JSON-RPC over stdio, until EOF.

    Nothing may be written to stdout except protocol frames — a stray print corrupts
    the stream and the client sees a hung server. Diagnostics go to stderr.

    `offload` runs the `OFFLOADED` tools in worker processes; by default only a real
    stdio session does, and a caller handing in its own streams gets the plain loop.
    At EOF the loop waits for its workers' answers before returning.
    """
    srv = Server(repo, called_from=called_from)
    _upgrade.adopt(srv)
    # A real stdio session reads its own descriptor, so a re-exec loses no request.
    inp = stdin or _upgrade.stdio_lines()
    outp = stdout or sys.stdout
    if offload is None:
        offload = stdin is None

    transport = StdioServer(
        lambda line: (outp.write(line + "\n"), outp.flush()),
        log=lambda text: print(text, file=sys.stderr),
    )
    notify = transport.notify
    srv.notify = notify
    relays: list = []

    def intercept(msg: dict[str, Any], send: Any) -> bool:
        nonlocal relays
        if not (offload and _offloaded(msg)):
            return False
        era = _modern_check(msg)
        if isinstance(era, dict):  # refused before a worker is spent on it
            send(era)
            return True
        # The worker's answer is already modern (it runs `handle`); this also shapes
        # the replies `_offload` makes itself -- busy, failed to start, no answer.
        reply = send if era is None else lambda f: send(_modernize("tools/call", f))
        relays = [r for r in relays if r.is_alive()]
        if (relay := _offload(srv, msg, reply, len(relays))) is not None:
            relays.append(relay)
        return True

    def after() -> None:
        if srv.snapshot_due is not None:  # a live handshake was answered: cache it
            text, srv.snapshot_due = srv.snapshot_due, None
            _write_snapshot(srv, text)
        if srv.kill_pending and _reexec_ready(inp, relays):
            _lifecycle.exit_process()  # a kill (local) or a reboot (remote): never returns
        elif srv.reexec_pending and _reexec_ready(inp, relays):
            _upgrade.execute(srv, PROC.replace_process)

    transport.serve(inp, srv.handle, intercept=intercept, after=after)
    for relay in relays:
        relay.join()


def _reexec_ready(inp, relays) -> bool:
    """May the process replace itself now? Only a real stdio session (`StdinLines`), with no
    offloaded call running and nothing read-but-unserved or waiting on the pipe."""
    idle = getattr(inp, "idle", None)
    return idle is not None and idle() and not any(r.is_alive() for r in relays)


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point: ``ddflow-mcp [--repo PATH]``.

    Kept argument-light on purpose. An MCP client spawns this with no arguments and a
    working directory, so the default path -- resolve the repository from the cwd, and
    from there to the PRIMARY checkout even if the cwd is a linked worktree -- has to
    be the one that needs no configuration. ``DDFLOW_REPO`` overrides for clients that
    spawn servers from a fixed directory.
    """

    cap_blas_threads()
    ap = argparse.ArgumentParser(
        prog="ddflow-mcp",
        description="ddflow MCP stdio server. Add to your agent's MCP config as:\n"
        '  {"mcpServers": {"ddflow": {"command": "uvx", '
        '"args": ["ddflow-mcp"]}}}',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--repo",
        default=os.environ.get("DDFLOW_REPO", ""),
        help="repository root (default: cwd, resolved to the primary checkout)",
    )
    ap.add_argument("--version", action="store_true")
    args = ap.parse_args(argv)
    if args.version:
        print(SERVER_INFO["version"])
        return 0

    start = Path(args.repo) if args.repo else Path.cwd()
    try:
        repo = repo_root(start)
    except Exception:
        # Not a git repository, or git is absent. Serve anyway: `ddflow_doctor` will
        # say so in a way the model can read and relay, which is far more useful than
        # a server that refuses to start and shows the client only "exited 1".
        repo = start.resolve()
    # `start` is passed ON, not discarded. It is the caller's ACTUAL location -- the
    # worktree an agent harness spawned this server in -- and `repo` is deliberately the
    # primary so every worktree shares one event log. Resolving and forgetting meant
    # `claim` over MCP never saw that the caller was already isolated, so worktree
    # ADOPTION was unreachable from the one surface the harnesses it was written for
    # actually drive.
    serve(repo, called_from=_upgrade.called_from() or start)
    return 0

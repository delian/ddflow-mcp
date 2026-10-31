"""The offload worker: run long tools in a worker PROCESS, and relay its frames.

A real stdio session runs every `OFFLOADED` tool in a separate process so a minutes-long
gate or reviewer does not block the loop and a crashed reviewer does not take the server
down. Split out of the old `surfaces/mcp.py` engine.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, Callable

from ...infra import proc as PROC
from .. import mcp_protocol as protocol
from ..mcp_protocol import err as _err
from ..mcp_protocol import ok as _ok
from ..tools import family_schema as _FAMILIES

from ._core import OFFLOADED
from ._reply import _text
from ._server import Server

#: Workers running at once ON ONE CONNECTION. A burst beyond it is answered "busy"
#: (exit 2) rather than started: each is a whole interpreter. Per connection, not per
#: machine: what it bounds is one client's burst.
MAX_WORKERS = 4


def _offloaded(msg: dict[str, Any]) -> bool:
    params = msg.get("params")
    return (
        msg.get("method") == "tools/call"
        and msg.get("id") is not None
        and isinstance(params, dict)
        and isinstance(params.get("name"), str)  # a list name must not end the loop
        # a family call is as long as the member it selects (`ddflow_gate action=run`)
        and _FAMILIES.member_of(params["name"], params.get("arguments")) in OFFLOADED
    )


def _worker_stderr(repo: Path):
    """Where a worker's diagnostics go: a log of its own, never the client's stderr pipe.

    Inherited, a client that disconnected left the worker writing to a broken pipe, and
    a gate or reviewer child got SIGPIPE before the run could record its outcome."""
    local = repo / ".ddflow" / "local"
    log = local / "mcp-workers.log"
    try:
        if (repo / ".ddflow").is_dir():
            local.mkdir(exist_ok=True)
            # Kept small: overwritten once past a megabyte rather than rotated.
            big = log.exists() and log.stat().st_size > 1 << 20
            return open(log, "w" if big else "a")  # the worker owns it
    except OSError as exc:
        print(f"ddflow mcp: worker log {log} unavailable ({exc})", file=sys.stderr)
    return PROC.DEVNULL


def _offload(srv: Server, msg: dict[str, Any], send: Callable[[dict[str, Any]], None], busy: int):
    """Run one call in a worker process; a thread relays its frames. Returns the thread.

    The worker is given the connection's identity and location -- the only per-connection
    state a tool reads (`client_info` is a label, the footer counters a courtesy). A
    `notifications/cancelled` is deliberately NOT passed on: a review or gate a client
    stopped waiting for still finishes and records, which is the point (B9abc247444).
    """
    mid = msg.get("id")
    name = (msg.get("params") or {}).get("name", "")
    if busy >= MAX_WORKERS:
        send(
            _ok(
                mid,
                _text(
                    f"{name}: {busy} long calls are already running on this connection "
                    f"(at most {MAX_WORKERS}). Call again when one has answered.",
                    meta={"exit": 2},
                ),
            )
        )
        return None
    job = {
        "repo": str(srv.repo),
        "called_from": str(srv.called_from),
        "agent": srv.agent,
        "msg": msg,
        "protocol": srv.protocol,
        # A `requestState` the worker issues must verify on the retry, which another
        # worker -- or this process -- serves (`mcp_protocol.state_key`).
        "state_key": protocol.state_key().hex(),
    }
    # The worker must import THIS code, not whatever ddflow the path would find first.
    root = str(Path(__file__).resolve().parents[3])
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(filter(None, [root, os.environ.get("PYTHONPATH", "")])),
    }
    err = _worker_stderr(srv.repo)
    try:
        p = PROC.popen(
            [sys.executable, "-c", "from ddflow.surfaces.mcp import _worker_main as m; m()"],
            stdin=PROC.PIPE,
            stdout=PROC.PIPE,
            stderr=err,
            text=True,
            env=env,
            start_new_session=True,
        )
        assert p.stdin is not None and p.stdout is not None
        p.stdin.write(json.dumps(job))
        p.stdin.close()
    except (OSError, ValueError) as exc:
        send(_ok(mid, _text(f"{name}: could not start its worker: {exc}", error=True)))
        return None
    finally:
        if err is not PROC.DEVNULL:
            err.close()  # the worker holds its own copy

    def relay() -> None:
        answered = False
        for raw in p.stdout:
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                print(raw, end="", file=sys.stderr)
                continue
            answered = answered or ("method" not in frame and frame.get("id") == mid)
            try:
                send(frame)
            except Exception:  # the client is gone; keep draining so the worker never blocks
                pass
        rc = p.wait()
        if not answered:
            try:
                send(
                    _ok(
                        mid,
                        _text(
                            f"{name}'s worker process ended (exit {rc}) without an answer. "
                            "This server is still up. What the run recorded is in the log: "
                            "check ddflow_gate_status.",
                            error=True,
                        ),
                    )
                )
            except Exception:
                pass

    t = threading.Thread(target=relay, name=f"ddflow-{name}-{mid}", daemon=True)
    t.start()
    return t


def _frame_writer(out) -> Callable[[dict[str, Any]], None]:
    """Write one JSON-RPC frame per call to ``out``; calls from several threads (the
    answer, a heartbeat that outlived its call) never interleave their bytes."""
    lock = threading.Lock()

    def write(frame: dict[str, Any]) -> None:
        with lock:
            try:
                out.write(json.dumps(frame) + "\n")
                out.flush()
            except (OSError, ValueError):
                pass

    return write


def _worker_main() -> None:
    """A worker process: one offloaded call, read as a job from stdin (see `_offload`).

    Protocol frames go to a private copy of stdout, and fd 1 is pointed at stderr, so a
    stray print or a child's output can never corrupt them. A parent that died is no
    reason to stop: writes to it are dropped and the run finishes and records.
    """
    job = json.loads(sys.stdin.read())
    out = os.fdopen(os.dup(1), "w")
    os.dup2(2, 1)
    sys.stdout = sys.stderr

    write = _frame_writer(out)

    msg = job["msg"]
    if job.get("state_key"):
        protocol.state_key(bytes.fromhex(job["state_key"]))
    srv = Server(Path(job["repo"]), job.get("agent", ""), called_from=Path(job["called_from"]))
    srv.protocol = job.get("protocol", srv.protocol)  # what the client negotiated, not the default
    srv.notify = write
    try:
        reply = srv.handle(msg)
    except BaseException as exc:  # the caller is owed an answer either way
        print(traceback.format_exc(), file=sys.stderr)
        reply = _err(msg.get("id"), -32603, f"internal error: {exc}")
    if reply is not None:
        write(reply)
    try:
        out.close()
    except OSError:
        pass

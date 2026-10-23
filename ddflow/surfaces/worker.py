"""The restartable MCP worker (SU-worker-proto): today's `Server` behind `worker_protocol` frames.

    python -m ddflow.surfaces.worker --repo PATH

The shim (SU-shim) spawns this as a child and talks to it over stdin/stdout; stderr is the
worker's log. Each `req` frame carries the connection's state, so the worker keeps none: a
fresh `Server` is built for every request (about a millisecond, R-su-worker-stateless), which is
what lets the shim replace the worker under a live connection.

A request never goes unanswered: a handler that raises is answered with a JSON-RPC internal
error, a malformed frame with an `error` frame, and the worker carries on. `drain` finishes
what runs, refuses anything new, then says `drained` and `bye`.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import traceback
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, TextIO

from . import ifacedigest as D
from . import mcp_protocol as protocol
from . import worker_protocol as WP
from .mcp import Server
from .mcp_upgrade import running_version

Handler = Callable[
    [dict[str, Any], dict[str, Any], Callable[[dict[str, Any]], None]], "dict[str, Any] | None"
]


def server_handler(repo: Path) -> Handler:
    """Serve a request with a `Server` built from the frame's connection state."""

    def handle(conn: dict[str, Any], msg: dict[str, Any], notify: Callable[[dict[str, Any]], None]):
        called_from = conn.get("called_from")
        srv = Server(
            repo, conn.get("agent", ""), called_from=Path(called_from) if called_from else None
        )
        if conn.get("protocol"):
            srv.protocol = conn["protocol"]
        srv.client_info = dict(conn.get("client_info") or {})
        srv.notify = notify
        return srv.handle(msg)

    return handle


def live_digest(repo: Path) -> str:
    """The interface digest of the code this worker runs (all tiers and the facade)."""
    surfaces = D.live_surfaces(Server(repo))
    return D.digest({"surfaces": {k: D.digest(v) for k, v in sorted(surfaces.items())}})


def _internal_error(msg: dict[str, Any], exc: BaseException) -> dict[str, Any] | None:
    if msg.get("id") is None:
        return None  # a notification: nothing is owed
    return {
        "jsonrpc": "2.0",
        "id": msg.get("id"),
        "error": {"code": -32603, "message": f"internal error: {exc}"},
    }


class _Run:
    """One worker session over a pair of streams."""

    def __init__(self, repo: Path, out: TextIO, handler: Handler | None, digest: str | None):
        self.repo = repo
        self.handler = handler or server_handler(repo)
        self.digest = digest
        self.out = out
        self.wlock = threading.Lock()
        self.cv = threading.Condition()
        self.inflight = 0
        self.draining = False
        self.said_bye = False
        self.done = threading.Event()
        self.drain_thread: threading.Thread | None = None
        self.exit_code = 0
        self.exit_reason = "eof"

    def write(self, frame: dict[str, Any]) -> None:
        with self.wlock:
            try:
                self.out.write(WP.encode(frame))
                self.out.flush()
            except (OSError, ValueError):
                pass  # the shim is gone; finish quietly

    def bye(self, reason: str) -> None:
        with self.wlock:
            if self.said_bye:
                return
            self.said_bye = True
        self.write(WP.bye(reason))

    def serve(self, rid: int, conn: dict[str, Any], msg: dict[str, Any]) -> None:
        try:
            try:
                reply = self.handler(conn, msg, lambda f: self.write(WP.note(rid, f)))
            except BaseException as exc:  # the caller is owed an answer either way
                print(traceback.format_exc(), file=sys.stderr)
                reply = _internal_error(msg, exc)
            self.write(WP.response(rid, reply))
        finally:
            with self.cv:
                self.inflight -= 1
                self.cv.notify_all()

    def start_request(self, frame: dict[str, Any]) -> None:
        with self.cv:
            self.inflight += 1
        try:
            threading.Thread(
                target=self.serve,
                args=(frame["id"], frame["conn"], frame["msg"]),
                name=f"ddflow-worker-{frame['id']}",
                daemon=True,
            ).start()
        except BaseException as exc:  # no thread: the caller is still owed an answer
            with self.cv:
                self.inflight -= 1
                self.cv.notify_all()
            self.write(
                WP.response(
                    frame["id"],
                    _internal_error(frame["msg"], exc)
                    or {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32603, "message": str(exc)},
                    },
                )
            )

    def wait_idle(self) -> None:
        with self.cv:
            self.cv.wait_for(lambda: self.inflight == 0)

    def start_drain(self) -> None:
        if self.draining:
            return
        self.draining = True

        def finish() -> None:
            self.wait_idle()
            self.write(WP.drained())
            self.bye("drained")
            self.done.set()

        t = threading.Thread(target=finish, name="ddflow-worker-drain", daemon=True)
        try:
            t.start()
        except BaseException:
            self.draining = False  # nothing is draining: the shim may try again
            raise
        self.drain_thread = t

    def hello(self, frame: dict[str, Any]) -> bool:
        err = WP.negotiate(frame)
        if err is not None:
            self.write(err)
            self.exit_code, self.exit_reason = 2, "refused"
            return False
        if frame.get("state_key"):
            protocol.state_key(bytes.fromhex(frame["state_key"]))
        digest = self.digest if self.digest is not None else live_digest(self.repo)
        self.write(WP.worker_hello(digest=digest, version=running_version()))
        return True

    def frame(self, frame: dict[str, Any], greeted: bool) -> bool | None:
        """Handle one frame. Returns the new `greeted`, or None to stop."""
        kind = frame["t"]
        if not greeted:
            if kind == "hello":
                return True if self.hello(frame) else None
            self.write(WP.error("no_hello", "the first frame must be a hello", id=frame.get("id")))
            return greeted
        if kind == "req":
            if self.draining:
                self.write(WP.error("draining", "this worker is draining", id=frame["id"]))
                return greeted
            self.start_request(frame)
        elif kind == "drain":
            self.start_drain()
        elif kind == "bye":
            self.exit_code, self.exit_reason = 0, "bye"
            return None
        else:
            self.write(WP.error("bad_frame", f"unexpected {kind!r} frame", id=frame.get("id")))
        return greeted


def run(
    repo: Path,
    lines: Iterable[str],
    out: TextIO,
    *,
    handler: Handler | None = None,
    digest: str | None = None,
    done: threading.Event | None = None,
) -> int:
    """Serve frames from `lines` until EOF, a `bye`, or a refused hello; returns the exit code.

    `done` is set once a drain has completed, for a caller whose `lines` can block forever.
    """
    r = _Run(Path(repo), out, handler, "" if digest is None and handler is not None else digest)
    if done is not None:
        r.done = done
    greeted = False
    for raw in lines:
        try:
            frame = WP.decode(raw)
        except WP.ProtocolError as exc:
            r.write(WP.error(exc.code, str(exc), id=exc.id))
            continue
        if frame is None:
            continue
        try:
            greeted = r.frame(frame, greeted)
        except Exception as exc:  # a bug in the dispatch must not take the connection down
            print(traceback.format_exc(), file=sys.stderr)
            r.write(WP.error("internal", f"internal error: {exc}", id=frame.get("id")))
            continue
        if greeted is None:
            break
    if r.drain_thread is not None:
        r.drain_thread.join()  # its `drained` and `bye` come first
    r.wait_idle()
    r.bye(r.exit_reason)
    return r.exit_code


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ddflow-worker")
    ap.add_argument("--repo", required=True)
    args = ap.parse_args(argv)
    # Frames go to a private copy of stdout; fd 1 points at stderr so a stray print or a
    # child's output can never corrupt them.
    out = os.fdopen(os.dup(1), "w")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    done = threading.Event()
    code: list[int] = []
    t = threading.Thread(
        target=lambda: code.append(run(Path(args.repo), sys.stdin, out, done=done)), daemon=True
    )
    t.start()
    while t.is_alive() and not done.is_set():
        t.join(0.1)
    if code:
        return code[0]
    # No result: either run() died (a crash) or a completed drain stopped us while stdin was open.
    return 0 if done.is_set() else 1


if __name__ == "__main__":
    sys.exit(main())

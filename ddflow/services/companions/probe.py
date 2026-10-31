"""Launch a companion and require a JSON-RPC answer — the part detection never does.

Split out of ``ddflow/services/companions.py``: ``verify``/``verify_one`` spawn the
server (opt-in, never called by ``scan``) and check it speaks MCP. ``scan`` and the
report read a config file and, at most, run a bounded probe (see ``.model``).
"""

from __future__ import annotations

import contextlib
import json
import os
import select
import shutil
import signal
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from ...infra import fsio
from ...infra import proc as P
from .model import _ANSI, Companion, load

#: How long `verify` waits for an `initialize` answer. Longer than a detection probe: the
#: launch command is the real server, and an `npx` one downloads itself on a cold cache.
VERIFY_TIMEOUT_S = 30

#: The protocol revision `verify` offers. A server that wants another answers with its own,
#: and that still proves it speaks MCP, so the exact value is not what is being tested.
_VERIFY_PROTOCOL = "2025-03-26"

#: Most of a server's output kept while waiting for its answer.
_MAX_BUF = 1 << 20

#: What an echoing binary (`cat`) sends back: our own request, recognisable by its method.
_ECHO_MARK = b'"method": "initialize"'

#: How long to keep polling for a child's exit once its stdout has closed. The pipe's EOF
#: can be read before the waitable status exists: a process closes its descriptors during
#: interpreter shutdown, a moment ahead of exiting (B297ede2447). A poll bounded by this
#: grace recovers the exit code; a child that merely closed stdout and stayed alive lapses.
_EXIT_GRACE_S = 1.0


@dataclass
class Verification:
    """The result of launching one companion and speaking MCP to it.

    ``speaks_mcp`` is three-valued for the reason `is_installed` is: ``True`` (it answered
    `initialize`), ``False`` (a FACT: the command is absent, exited, or answered something
    that is not a JSON-RPC response -- `cat` echoes the request back, which is not an
    answer), ``None`` (could not tell: no answer in time, e.g. a cold `npx` cache).
    """

    companion: Companion
    speaks_mcp: bool | None
    detail: str
    server: dict = field(default_factory=dict)
    elapsed_s: float = 0.0


def _reply_to(buf: bytes, want_id: int) -> tuple[dict | None, bytes]:
    """The first complete JSON-RPC *response* to `want_id` in `buf`, and the rest.

    A response has `result` or `error` and no `method`: a request echoed back (`cat`) or a
    server's own notification is not one. Lines that are not JSON are skipped -- a server
    may log to stdout before it speaks.
    """
    while b"\n" in buf:
        line, buf = buf.split(b"\n", 1)
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if (
            isinstance(msg, dict)
            and msg.get("jsonrpc") == "2.0"
            and msg.get("id") == want_id
            and "method" not in msg
            and ("result" in msg or "error" in msg)
        ):
            return msg, buf
    return None, buf


def _describe_answer(msg: dict) -> tuple[str, dict]:
    """(detail, server facts) for a JSON-RPC response to `initialize`."""
    result = msg.get("result")
    if not isinstance(result, dict):
        return "answered initialize with a JSON-RPC error (it speaks the protocol)", {}
    info = result.get("serverInfo")
    server = dict(info) if isinstance(info, dict) else {}
    if result.get("protocolVersion"):
        server["protocolVersion"] = result["protocolVersion"]
    who = " ".join(str(server[k]) for k in ("name", "version") if server.get(k))
    return f"answered initialize ({who or 'no serverInfo'})", server


def _exit_info(proc: P.Popen) -> os.waitid_result | None:
    """The direct child's exit record, or None while it runs. WITHOUT reaping it
    (`poll`/`wait` would).

    An unreaped leader keeps its pid -- and so the process group's id -- reserved, which is
    what makes the final group-wide SIGKILL safe: once reaped, the pid may be recycled
    and `killpg` would hit whatever got it.
    """
    try:
        return os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    except ChildProcessError:
        return None


def _await_exit(proc: P.Popen, grace_s: float) -> os.waitid_result | None:
    """The child's exit record once it is visible, or None if it is still alive after
    `grace_s`. A pipe's EOF can be read before the process that closed it is waitable: the
    descriptors close during interpreter shutdown, a moment ahead of the exit, so a single
    `_exit_info` sample can miss the code (B297ede2447). Poll WITHOUT reaping, so the
    leader's pid stays reserved for the final group SIGKILL.
    """
    deadline = time.monotonic() + grace_s
    info = _exit_info(proc)
    while info is None and time.monotonic() < deadline:
        time.sleep(0.01)
        info = _exit_info(proc)
    return info


def _exit_how(info: os.waitid_result | None) -> str:
    """How the child ended, for the failure detail: an exit code, the signal that killed
    it (si_status is then the signal, not a code), or that it merely closed its output."""
    if info is None:
        return "closed its output"
    if info.si_code in (os.CLD_KILLED, os.CLD_DUMPED):
        return f"killed by signal {info.si_status}"
    return f"exited ({info.si_status})"


def _stop(proc: P.Popen) -> None:
    """End the launched server and EVERYTHING it started (an `npx` wrapper has children).

    SIGTERM to the process group, a short grace for the direct child, then SIGKILL to the
    group unconditionally -- the direct child exiting says nothing about a grandchild that
    ignores SIGTERM -- and only then reap the leader.
    """
    P.kill_group(proc, signal.SIGTERM)
    _await_exit(proc, 3.0)
    P.kill_group(proc)
    with contextlib.suppress(P.TimeoutExpired):
        proc.wait(timeout=5)


def _keep(buf: bytes, chunk: bytes) -> bytes:
    """`buf` plus `chunk`, truncated to the last `_MAX_BUF` bytes (a newline-free flood must
    not grow the buffer, or make each read copy all of it)."""
    return (buf + chunk)[-_MAX_BUF:]


def verify_one(c: Companion, *, timeout_s: float = VERIFY_TIMEOUT_S) -> Verification:
    """Launch `command args` and require a JSON-RPC answer to `initialize`.

    The only check that tells a server from a binary with a plausible name (B113 was a
    companion whose launch command was not an MCP server at all). It SPAWNS a process, so
    it is opt-in and is never called by `scan`: the MCP handshake calls `scan`.
    """
    t0 = time.monotonic()

    def done(ok: bool | None, detail: str, server: dict | None = None) -> Verification:
        return Verification(c, ok, detail, server or {}, round(time.monotonic() - t0, 2))

    if not c.is_mcp:
        return done(None, f"{c.id} is a {c.kind} companion: there is no MCP server to launch")
    if not c.command:
        return done(False, "no launch command declared")
    exe = shutil.which(c.command)
    if not exe:
        return done(False, f"`{c.command}` is not on PATH")
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": _VERIFY_PROTOCOL,
            "capabilities": {},
            "clientInfo": {"name": "ddflow-verify", "version": "0"},
        },
    }
    with fsio.scratch_file() as err:
        try:
            proc = P.spawn_stdio([exe, *c.args], env={**os.environ, **c.env}, stderr=err)
        except OSError as exc:
            return done(False, f"could not launch `{c.command}`: {exc}")

        def stderr_tail() -> str:
            err.seek(0, os.SEEK_END)
            err.seek(max(0, err.tell() - 8192))  # the tail only: a chatty server's log is big
            lines = [x.strip() for x in err.read().decode("utf-8", "replace").splitlines()]
            lines = [x for x in (_ANSI.sub("", x) for x in lines) if x]
            return f" stderr: {lines[-1][:160]}" if lines else ""

        try:
            try:
                assert proc.stdin is not None and proc.stdout is not None
                proc.stdin.write((json.dumps(request) + "\n").encode())
                proc.stdin.flush()
            except OSError:
                return done(False, f"`{c.command}` exited before reading a request.{stderr_tail()}")
            fd = proc.stdout.fileno()
            buf, deadline, echoed_at = b"", t0 + timeout_s, 0.0
            while True:
                left = deadline - time.monotonic()
                if left <= 0 and echoed_at:
                    return done(
                        False,
                        f"`{c.command}` echoed the request back and sent no answer within "
                        f"{timeout_s:g}s: not a server",
                    )
                if left <= 0:
                    return done(
                        None,
                        f"no answer to `initialize` within {timeout_s:g}s -- could not tell "
                        f"(a cold `npx` cache is slow; a binary that is not a server never answers)."
                        + stderr_tail(),
                    )
                ready, _, _ = select.select([fd], [], [], min(left, 0.5))
                if not ready:
                    continue  # the pipe is still open: a wrapper's child may yet answer
                chunk = os.read(fd, 65536)
                if not chunk:
                    break  # EOF: nothing holds the pipe any more
                buf = _keep(buf, chunk)
                if not echoed_at and _ECHO_MARK in buf:
                    echoed_at = time.monotonic()  # a log line may precede the (slow) answer
                msg, buf = _reply_to(buf, 1)
                if msg is not None:
                    return done(True, *_describe_answer(msg))
            from . import (
                _EXIT_GRACE_S,  # late-bound: honours monkeypatch of the package attr
                _await_exit,  # late-bound: honours monkeypatch of the package attr
            )

            grace = min(_EXIT_GRACE_S, max(0.0, deadline - time.monotonic()))
            how = _exit_how(_await_exit(proc, grace))
            return done(
                False, f"`{c.command}` {how} without answering `initialize`." + stderr_tail()
            )
        finally:
            _stop(proc)


def verify(
    repo: Path, ids: list[str] | None = None, *, timeout_s: float = VERIFY_TIMEOUT_S
) -> list[Verification]:
    """Launch every MCP companion (or just `ids`) and check it speaks MCP, concurrently.

    NEVER called from `scan`. Concurrent because each launch may be an `npx` cold start.
    """
    comps = [c for c in load(repo) if c.is_mcp and (ids is None or c.id in ids)]
    if not comps:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(comps))) as ex:
        return list(ex.map(lambda c: verify_one(c, timeout_s=timeout_s), comps))

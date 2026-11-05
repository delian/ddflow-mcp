"""The capture side of `ddflow harness record`: what the agent under test runs, in a scratch project.

``python -m ddflow.services.harness_tap hook <capture> <event>`` is a hook command: it keeps the
agent's stdin as it came and prints nothing. ``... mcp <capture> -- <server argv>`` stands
between the agent and the real MCP server and logs every line both ways. Neither reads, parses
or judges a payload: scrubbing happens later, in `harness_record`, before anything is committed.

Two properties are load-bearing:

* **It never fails the turn it observes.** A hook that exits non-zero blocks some agents, so
  a tap that cannot store exits 0 and says so on stderr; a full MCP log stops logging, not
  passing bytes.
* **Raw payloads stay private.** They may hold anything the session touched: the capture
  directory is the owner's alone, and it is never committed.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import BinaryIO

from ..infra import proc

#: Hook payloads kept per capture: a short session is what a recording is.
MAX_EVENTS = 500
#: Bytes kept of one hook payload; a longer one is cut, and so does not parse: `record` says so.
MAX_PAYLOAD_BYTES = 1 << 20
#: Bytes of `mcp.jsonl`. Past it the log stops; the bytes keep flowing.
MAX_MCP_BYTES = 8 << 20
#: How long a server whose agent has gone gets to exit by itself.
STOP_GRACE_S = 5
HOOKS_DIR = "hooks"
MCP_LOG = "mcp.jsonl"
PRIVATE_DIR = 0o700
PRIVATE_FILE = 0o600


def store_hook(capture: Path, event: str, raw: bytes) -> bool:
    """Keep one payload as its own file, named so a sort is the order they fired in."""
    d = capture / HOOKS_DIR
    d.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR)
    if sum(1 for _ in d.iterdir()) >= MAX_EVENTS:
        return False
    path = d / f"{time.time_ns()}-{os.getpid()}-{event}.json"
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, PRIVATE_FILE)
    with os.fdopen(fd, "wb") as f:
        f.write(raw)
    return True


def hook(capture: Path, event: str, stdin: BinaryIO) -> int:
    """Always 0, whatever happens: see the module note."""
    try:
        raw = stdin.read(MAX_PAYLOAD_BYTES)
        try:
            if not store_hook(capture, event, raw):
                print(f"harness tap: {MAX_EVENTS} payloads kept, {event} dropped", file=sys.stderr)
        except OSError as exc:
            print(f"harness tap: {event} not stored: {exc}", file=sys.stderr)
        while stdin.read(MAX_PAYLOAD_BYTES):  # an agent may fail on a pipe closed under it
            pass
    except (OSError, ValueError) as exc:
        print(f"harness tap: {event}: stdin: {exc}", file=sys.stderr)
    return 0


class McpLog:
    """`mcp.jsonl`, one object per line: direction and text. Two pumps share it. A log that
    cannot be opened logs nothing: the agent still gets its server."""

    def __init__(self, capture: Path) -> None:
        self._lock = threading.Lock()
        self._written = 0
        self._f: BinaryIO | None = None
        try:
            capture.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR)
            fd = os.open(capture / MCP_LOG, os.O_CREAT | os.O_APPEND | os.O_WRONLY, PRIVATE_FILE)
            self._f = os.fdopen(fd, "ab")
            self._written = os.fstat(fd).st_size  # a restarted agent adds to the same file
        except OSError as exc:
            print(f"harness tap: no mcp log: {exc}", file=sys.stderr)

    def note(self, direction: str, line: bytes) -> None:
        if self._f is None:
            return
        text = line.decode("utf-8", "replace").rstrip("\r\n")
        row = (json.dumps({"d": direction, "line": text}) + "\n").encode()
        with self._lock:
            if self._written + len(row) > MAX_MCP_BYTES:
                return
            self._written += len(row)
            self._f.write(row)
            self._f.flush()


def _pump(src: BinaryIO, dst: BinaryIO, log: McpLog, direction: str) -> None:
    """Line by line until EOF. Logged before it passes: the tap leaves the moment the server
    exits, so a line must be on disk before anything can answer it. A logging failure never
    stops the bytes."""
    try:
        for line in iter(src.readline, b""):
            try:
                log.note(direction, line)
            except (OSError, ValueError) as exc:
                print(f"harness tap: mcp log: {exc}", file=sys.stderr)
            dst.write(line)
            dst.flush()
    except (BrokenPipeError, ValueError):
        pass  # the other end went away: the session is over
    finally:
        try:
            dst.close()
        except OSError:
            pass


def mcp(capture: Path, argv: list[str]) -> int:
    """Run the server and stand between it and the agent, until the server exits."""
    log = McpLog(capture)
    child = proc.spawn_stdio(argv, stderr=None)
    assert child.stdin is not None and child.stdout is not None
    up = threading.Thread(
        target=_pump, args=(sys.stdin.buffer, child.stdin, log, "in"), daemon=True
    )
    up.start()
    _pump(child.stdout, sys.stdout.buffer, log, "out")
    try:
        return child.wait(timeout=STOP_GRACE_S)
    except proc.TimeoutExpired:  # the agent left and nothing reads the server's output now
        proc.stop_group(child)
        return 0


def main(argv: list[str]) -> int:
    verb, capture, *rest = [*argv, "", ""]
    if verb == "hook" and rest[0] and len(argv) == len(("hook", "dir", "event")):
        return hook(Path(capture), rest[0], sys.stdin.buffer)
    if verb == "mcp" and rest[0] == "--" and argv[3:]:
        # Not a normal exit: the upstream pump is a daemon blocked in a read of stdin, and the
        # interpreter's shutdown aborts on that lock. Everything is written by now.
        os._exit(mcp(Path(capture), argv[3:]))
    print("usage: harness_tap hook <capture> <event> | mcp <capture> -- <argv>", file=sys.stderr)
    return 1 if verb == "mcp" else 0  # a hook never fails; a server that did not start does


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

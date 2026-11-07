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
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO, NoReturn

from ..infra import fsio, proc

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
    count = sum(1 for _ in d.iterdir())
    while count < MAX_EVENTS:
        try:
            path = d / f"{count:06d}-{event}.json"
            fsio.atomic_write(path, raw, mode=PRIVATE_FILE, exclusive=True, fsync=False)
            return True
        except FileExistsError:  # a hook of the same event fired beside this one
            count += 1
    return False


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
            path = capture / MCP_LOG
            self._f = path.open("ab")
            path.chmod(PRIVATE_FILE)
            self._written = path.stat().st_size  # a restarted agent adds to the same file
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


def _guarded(log: McpLog) -> Callable[[str, bytes], None]:
    """``log.note`` that only ever complains: a logging failure must not stop the bytes."""

    def note(direction: str, line: bytes) -> None:
        try:
            log.note(direction, line)
        except (OSError, ValueError) as exc:
            print(f"harness tap: mcp log: {exc}", file=sys.stderr)

    return note


def mcp(capture: Path, argv: list[str]) -> NoReturn:
    """Run the server and stand between it and the agent, until the server exits."""
    proc.relay_stdio(argv, _guarded(McpLog(capture)), grace_s=STOP_GRACE_S)


def main(argv: list[str]) -> int:
    verb, capture, *rest = [*argv, "", ""]
    if verb == "hook" and rest[0] and len(argv) == len(("hook", "dir", "event")):
        return hook(Path(capture), rest[0], sys.stdin.buffer)
    if verb == "mcp" and rest[0] == "--" and argv[3:]:
        mcp(Path(capture), argv[3:])
    print("usage: harness_tap hook <capture> <event> | mcp <capture> -- <argv>", file=sys.stderr)
    return 1 if verb == "mcp" else 0  # a hook never fails; a server that did not start does


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

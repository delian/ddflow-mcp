#!/usr/bin/env python3
"""Client-matrix probe: a stdlib stdio MCP server that logs every message and changes its
tool list on demand, plus the classifier that reads the log back.

    python -m ddflow.mcpshim.probe_server LOGFILE [--delay SECONDS]

Tools: `bump` adds `secret_oracle` and sends `notifications/tools/list_changed` at once;
`delaybump` adds it and notifies SECONDS later (default 6) while the client is idle. The new
tool's name is never disclosed in a result, so the model has to find it through its tool list.
A client honours list_changed iff the log shows `tools/list` AFTER the notification
(`analyze_log`; research note R-su-probe-logrule). Appendix A of
docs/ddflow/research/mcp-client-matrix.md is the unproductised original.
"""

from __future__ import annotations

import json
import queue
import sys
import threading
import time

DEFAULT_DELAY_S = 6.0
IN_LIMIT = 2000
OUT_LIMIT = 300
_FIELDS = 3
SETTLE_S = 0.02
NOTE = "notifications/tools/list_changed"

_BASE = [
    {
        "name": "bump",
        "description": "Call this: the server adds one new tool and sends "
        "notifications/tools/list_changed immediately.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "delaybump",
        "description": "Call this: the server adds one new tool and sends list_changed "
        "a few seconds LATER (unsolicited).",
        "inputSchema": {"type": "object", "properties": {}},
    },
]
_ADDED = {
    "name": "secret_oracle",
    "description": "Returns the word MAGENTA. Only exists after bump or delaybump.",
    "inputSchema": {"type": "object", "properties": {}},
}


def _text(i, text):
    return {"jsonrpc": "2.0", "id": i, "result": {"content": [{"type": "text", "text": text}]}}


class _Probe:
    """The probe's state: the tool list, the log, and the replies."""

    def __init__(self, stdout, logfile, delay: float):
        self.lock = threading.Lock()
        self.tools = list(_BASE)
        self.stdout, self.logfile, self.delay = stdout, logfile, delay

    def _stamp(self, direction: str, text: str) -> None:
        self.logfile.write(f"{time.time() % 1000:.2f} {direction} {text}\n")
        self.logfile.flush()

    def log_in(self, text: str) -> None:
        with self.lock:
            self._stamp("IN ", text[:IN_LIMIT])

    def send(self, m: dict) -> None:
        with self.lock:
            self.stdout.write(json.dumps(m) + "\n")
            self.stdout.flush()
            self._stamp("OUT", json.dumps(m)[:OUT_LIMIT])

    def _later(self) -> None:
        time.sleep(self.delay)
        self.send({"jsonrpc": "2.0", "method": NOTE})

    def _call(self, i, name) -> None:
        if name not in ("bump", "delaybump"):
            self.send(_text(i, "MAGENTA"))
            return
        if _ADDED not in self.tools:
            self.tools.append(_ADDED)
        self.send(
            _text(
                i,
                "server tool list changed (new tool name not disclosed; "
                "list your tools to find it)",
            )
        )
        if name == "bump":
            self.send({"jsonrpc": "2.0", "method": NOTE})
        else:
            threading.Thread(target=self._later, daemon=True).start()

    def handle(self, m: dict) -> None:
        meth, i, params = m.get("method"), m.get("id"), m.get("params") or {}
        results = {
            "initialize": lambda: {
                "protocolVersion": params.get("protocolVersion"),
                "capabilities": {"tools": {"listChanged": True}, "prompts": {"listChanged": True}},
                "serverInfo": {"name": "lc", "version": "1"},
            },
            "tools/list": lambda: {"tools": self.tools},
            "prompts/list": lambda: {"prompts": []},
            "resources/list": lambda: {"resources": []},
        }
        if meth == "tools/call":
            self._call(i, params.get("name"))
        elif meth in results:
            self.send({"jsonrpc": "2.0", "id": i, "result": results[meth]()})
        elif i is not None:
            self.send({"jsonrpc": "2.0", "id": i, "result": {}})


def serve(stdin, stdout, logfile, delay: float = DEFAULT_DELAY_S) -> None:
    """Log each request when it ARRIVES and handle it on another thread, so a request a client
    pipelined before it could see a notification is logged before that notification."""
    probe = _Probe(stdout, logfile, delay)
    work: queue.Queue = queue.Queue()

    def handler() -> None:
        while (line := work.get()) is not None:
            time.sleep(SETTLE_S)  # let already-pipelined input reach the log first
            try:
                probe.handle(json.loads(line))
            except ValueError:
                continue

    t = threading.Thread(target=handler)
    t.start()
    for line in stdin:
        if line.strip():
            probe.log_in(line.strip())
            work.put(line)
    work.put(None)
    t.join()


def _parse(log: str):
    """(direction, message) for each parseable log line; truncated or junk lines are skipped."""
    for line in log.splitlines():
        parts = line.split(None, 2)
        if len(parts) < _FIELDS or parts[1] not in ("IN", "OUT"):
            continue
        try:
            yield parts[1], json.loads(parts[2])
        except ValueError:
            continue


def analyze_log(log: str) -> dict:
    """The client-table row a probe log supports.

    list_changed: `honours` (a tools/list followed the notification), `ignores` (the
    notification went out and none did), `unknown` (it never went out: nothing to conclude).
    """
    notified_at = None
    refetched = False
    row = {"client": None, "revision": None, "probed_discover": False, "listen": False}
    for n, (direction, m) in enumerate(_parse(log)):
        meth = m.get("method")
        if direction == "OUT":
            if meth == NOTE and notified_at is None:
                notified_at = n
            continue
        if meth == "initialize":
            p = m.get("params") or {}
            info = p.get("clientInfo") or {}
            row["revision"] = p.get("protocolVersion")
            if info:
                row["client"] = {"name": info.get("name"), "version": info.get("version")}
        elif meth == "server/discover":
            row["probed_discover"] = True
        elif meth == "subscriptions/listen":
            row["listen"] = True
        elif meth == "tools/list" and notified_at is not None:
            refetched = True
    row["list_changed"] = (
        "unknown" if notified_at is None else ("honours" if refetched else "ignores")
    )
    return row


def main(argv: list[str]) -> int:
    args = list(argv)
    delay = DEFAULT_DELAY_S
    if "--delay" in args:
        k = args.index("--delay")
        try:
            delay = float(args[k + 1])
        except (IndexError, ValueError):
            args = []
        else:
            del args[k : k + 2]
    if len(args) != 1:
        sys.stderr.write("usage: probe_server LOGFILE [--delay SECONDS]\n")
        return 2
    with open(args[0], "a") as logfile:
        serve(sys.stdin, sys.stdout, logfile, delay)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

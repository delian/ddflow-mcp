"""Does an ACP agent honour an injected MCP server and the session cwd? (CP-verify-facts)

    python3 -I probes/probe_acp_mcp_inject.py 'kilo acp' [seconds]

Sends initialize and session/new, never a prompt, so no model is called. The stub MCP server
this file also is logs its cwd and an env marker; the agent starts in one temporary directory
and the session names another, so the log tells them apart. HOME and XDG_* point at a third:
the operator's own config and sessions are neither read nor written, and an agent that needs
a login there says so in its session/new error. The marker is new each run and absent from the
agent's own environment, so only the injected `env` can deliver it. Exit 0 only when the server
started in the session directory with that marker, the agent answered the initialize result
(an agent sends `notifications/initialized` only after reading it) and it listed the tools. It
tests a real agent's behaviour, not one built to fool it. MCP stdio is newline-delimited JSON.
"""

import json
import os
import secrets
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

MARKER = "CP_PROBE_MARKER"


def stub(log: str) -> None:
    def note(**kw: object) -> None:
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(kw) + "\n")

    note(event="started", cwd=os.getcwd(), marker=os.environ.get(MARKER))
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            note(event="unreadable", line=line[:80])
            continue
        note(event="request", method=msg.get("method"))
        if "id" not in msg:
            continue
        if msg["method"] == "initialize":
            info = {"name": "cp-probe", "version": "0"}
            result = {
                "protocolVersion": msg["params"].get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": info,
            }
        elif msg["method"] == "tools/list":
            schema = {"type": "object", "properties": {}}
            result = {"tools": [{"name": "ping", "description": "ping", "inputSchema": schema}]}
        else:
            result = {}
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)


def ask(proc: subprocess.Popen, lines: list[str], rid: int, method: str, params: dict, wait: float):
    try:
        proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        )
        proc.stdin.write("\n")
        proc.stdin.flush()
    except BrokenPipeError:  # the agent is gone: no answer, reported by the caller
        return None
    end = time.monotonic() + wait
    while time.monotonic() < end:
        for line in list(lines):
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("id") == rid and ("result" in msg or "error" in msg):
                return msg
        time.sleep(0.1)
    return None


def judge(entries: list[dict], session: str, spawn: str, marker: str) -> dict:
    started = [e for e in entries if e.get("event") == "started"]
    methods = {e.get("method") for e in entries}
    cwd = started[0]["cwd"] if started else None
    return {
        "started": bool(started),
        "env_passed": bool(started) and started[0].get("marker") == marker,
        "initialized": {"initialize", "notifications/initialized"} <= methods,
        "tools_listed": "tools/list" in methods,
        "cwd_is_session": cwd == session,
        "cwd_is_spawn": cwd == spawn,
    }


def probe(agent: str, wait: float) -> int:
    with tempfile.TemporaryDirectory(prefix="acp-probe-") as tmp:
        base = Path(tmp).resolve()
        spawn, session, home = base / "spawn", base / "session", base / "home"
        for d in (spawn, session, home):
            d.mkdir()
        log = str(base / "stub.log")
        marker = secrets.token_hex(8)
        env = {k: v for k, v in os.environ.items() if k != MARKER}
        env["HOME"] = str(home)
        for k, sub in (
            ("CONFIG", ".config"),
            ("DATA", ".data"),
            ("CACHE", ".cache"),
            ("STATE", ".state"),
        ):
            env[f"XDG_{k}_HOME"] = str(home / sub)
        proc = subprocess.Popen(
            shlex.split(agent),
            cwd=spawn,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        lines: list[str] = []
        threading.Thread(target=lambda: lines.extend(proc.stdout), daemon=True).start()
        caps = {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False}
        init = ask(
            proc, lines, 1, "initialize", {"protocolVersion": 1, "clientCapabilities": caps}, wait
        )
        server = {
            "name": "cpprobe",
            "command": sys.executable,
            "args": ["-I", str(Path(__file__).resolve()), "--stub", log],
            "env": [{"name": MARKER, "value": marker}],
        }
        new = ask(
            proc, lines, 2, "session/new", {"cwd": str(session), "mcpServers": [server]}, wait
        )
        time.sleep(min(wait, 8))
        proc.terminate()
        try:
            proc.stdin.close()
        except BrokenPipeError:  # the agent left first; its unread bytes do not matter
            pass
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
        text = Path(log).read_text(encoding="utf-8") if os.path.exists(log) else ""
        entries = [json.loads(line) for line in text.splitlines()]
        seen = judge(entries, str(session), str(spawn), marker)
        capabilities = ((init or {}).get("result") or {}).get("agentCapabilities", {})
        print(json.dumps({"mcpCapabilities": capabilities.get("mcpCapabilities")}))
        print(json.dumps({"session_error": (new or {"error": "no answer"}).get("error")}))
        print(json.dumps(seen))
        need = ("started", "env_passed", "initialized", "tools_listed", "cwd_is_session")
        return 0 if all(seen[k] for k in need) else 1


if __name__ == "__main__":
    if sys.argv[1:2] == ["--stub"]:
        stub(sys.argv[2])
    else:
        sys.exit(probe(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 60.0))

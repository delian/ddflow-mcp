"""No child process may inherit the MCP server's stdin.

ddflow speaks MCP over **stdio**: the JSON-RPC session is literally this process's
stdin and stdout. `subprocess.run(...)` with no explicit `stdin=` hands the child that
same pipe, so any child that reads stdin — an arbitrary shell command in a gate, a
reviewer CLI, an `npx` that wants to prompt — eats the bytes the protocol needed, or
closes the descriptor outright.

The failure is as quiet as a failure gets. The server exits **zero**, stderr is
**empty**, and the client sees a closed stream with nothing at all to explain it. It
was found when a companion-detection probe added to `ddflow setup` ended the MCP
session on the *next* tool call, and it had been latent in all twelve subprocess call
sites since the beginning: `gate run` executes an arbitrary project command, which is
the one place a stdin-reading child is not merely possible but likely.

Two tests, because they fail for different reasons and both are needed: one proves the
behaviour end to end through a real server, and one is the ratchet that stops the
twelfth call site from becoming a thirteenth.
"""

from __future__ import annotations

import json
import os
import re
import selectors
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3
ROOT = Path(__file__).resolve().parents[1]


def _server(repo: Path) -> subprocess.Popen:
    env = {**os.environ, "PYTHONPATH": str(ROOT), "DDFLOW_AGENT": "stdio-test"}
    return subprocess.Popen(
        [sys.executable, "-m", "ddflow", "--repo", str(repo), "mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=env,
    )


#: A bounded read, because the failure mode under test is a HANG as often as it is a
#: closed stream: the child sits holding our stdin and both processes wait at 0% CPU
#: looking perfectly healthy. A test that reproduces that by hanging forever is a test
#: that wedges the suite instead of reporting the bug, so the wait is explicit.
_REPLY_TIMEOUT_S = 45


def _rpc(proc, method: str, params: dict | None = None, rid: int | None = None):
    msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if rid is not None:
        msg["id"] = rid
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()
    if rid is None:
        return None
    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ)
    try:
        if not sel.select(_REPLY_TIMEOUT_S):
            return ""  # neither a reply nor EOF: the child took the pipe and held it
        return proc.stdout.readline()
    finally:
        sel.close()


def test_a_gate_command_that_reads_stdin_does_not_end_the_mcp_session(repo):
    """The end-to-end proof, using the worst realistic child: `cat`.

    A gate command is arbitrary — it is whatever the project put in gates.toml. One
    that reads stdin used to consume the JSON-RPC stream and the session simply
    stopped, with a zero exit code and an empty stderr on both sides.
    """
    run_cli(repo, "init")
    (repo / ".ddflow" / "gates.toml").write_text(
        '[gate.unit_tests]\ncommand = "cat"\ncwd = "repo"\n'
    )
    run_cli(repo, "phase", "add", "P1", "--globs", "src/**")
    run_cli(repo, "task", "add", "P1.T1", "--phase", "P1", "--globs", "src/a.py")

    proc = _server(repo)
    try:
        _rpc(
            proc,
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "1"},
            },
            rid=1,
        )
        _rpc(proc, "notifications/initialized")
        ran = _rpc(
            proc,
            "tools/call",
            {"name": "ddflow_gate_run", "arguments": {"id": "P1.T1", "gate": "unit_tests"}},
            rid=2,
        )
        assert ran, (
            "no reply within the deadline: the server either died running the gate or "
            "is wedged waiting on a child that took stdin with it. "
            f"exit={proc.poll()}"
            # Deliberately NOT `proc.stderr.read()`. In the wedged variant — the one
            # this test exists for — the server is still alive and still holds the
            # write end, so `read()` waits for an EOF that never comes. The assert
            # message would hang the suite instead of reporting, which is precisely
            # what the bounded `select` above was added to prevent. The bounded read
            # only bounded the REPLY.
        )
        after = _rpc(proc, "tools/call", {"name": "ddflow_status", "arguments": {}}, rid=3)
        assert after, (
            "the gate returned but the NEXT call found a closed stream: the child "
            "consumed the buffered protocol bytes rather than killing the process, "
            f"which is the same bug one step later. exit={proc.poll()}"
        )
        assert json.loads(after)["id"] == 3
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_no_module_spawns_a_process_without_going_through_proc(repo):
    """The ratchet. Twelve call sites had this bug; a thirteenth must not be able to.

    Mechanically checkable, so per the house rule it ships as a check rather than as a
    lesson nobody greps. `proc.py` itself is the one place allowed to call the stdlib
    directly — that is what it is for.
    """
    # Every way to start a process, not the five that happened to be in use, and
    # `rglob` rather than `glob`: the first version matched `subprocess.run|Popen|call|
    # check_output|check_call` in top-level modules only, so `os.system`, a bare
    # `from subprocess import run`, an `asyncio` spawn, or anything in a future
    # subpackage walked straight past the check that exists to stop exactly that.
    spawn = re.compile(
        r"\bsubprocess\.(run|Popen|call|check_output|check_call|getoutput|getstatusoutput)\("
        r"|\bos\.(system|popen|exec[lv][ep]*|spawn[lv][ep]*)\("
        r"|\basyncio\.create_subprocess_(exec|shell)\("
        r"|^\s*from subprocess import"
    )
    offenders = []
    for path in sorted((ROOT / "ddflow").rglob("*.py")):
        if path.name == "proc.py":
            continue
        src = path.read_text("utf-8")
        for n, line in enumerate(src.splitlines(), 1):
            if spawn.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()}")
    assert not offenders, (
        "these spawn a child without the stdin guard; use `from . import proc as P` "
        "and `P.run`/`P.popen`:\n  " + "\n  ".join(offenders)
    )


def test_the_guard_actually_detaches_stdin():
    """Mutation-proof of the ratchet's premise, in the shape the bug actually takes.

    The obvious version of this test — call `P.run` from inside pytest and assert the
    child saw no input — PASSES WITH THE GUARD REMOVED, because pytest's own stdin is
    already empty, so the child reads '' either way. It proves nothing, which is the
    commonest way this gate is met on paper.

    So the parent here is a separate process with a REAL pipe on its stdin carrying
    real bytes, exactly like an MCP server mid-session. It calls `P.run` on a child
    that reads stdin, then reads its own stdin afterwards. With the guard, the child
    gets nothing and the parent still has its protocol bytes. Without it, the child
    swallows them and the parent is left with a stream it can no longer speak on.
    """
    parent = (
        f"import sys; sys.path.insert(0, {str(ROOT)!r});"
        "from ddflow.infra import proc as P;"
        "r = P.run([sys.executable, '-c', 'import sys; sys.stdout.write(sys.stdin.read())'],"
        "          capture_output=True, text=True, timeout=30);"
        "print('CHILD_SAW=' + repr(r.stdout));"
        "print('PARENT_KEPT=' + repr(sys.stdin.read()))"
    )
    p = subprocess.run(
        [sys.executable, "-c", parent],
        input="PROTOCOL-BYTES\n",
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert p.returncode == 0, p.stderr[-800:]
    assert "CHILD_SAW=''" in p.stdout, (
        f"the child inherited the parent's stdin and read the protocol off it: {p.stdout!r}"
    )
    assert "PARENT_KEPT='PROTOCOL-BYTES\\n'" in p.stdout, (
        f"the parent lost the bytes it needed: {p.stdout!r}"
    )


def test_an_explicit_input_still_reaches_the_child():
    """The legitimate case must keep working: a reviewer CLI fed a diff on stdin.

    `input=` gives the child its own pipe rather than ours, so it is safe — and
    setting both `input` and `stdin` is a TypeError, which is how this nearly shipped
    as a regression in the reviewer backend.
    """
    from ddflow.infra import proc as P

    fed = P.run(
        [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"],
        input="a diff",
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert fed.stdout == "a diff", fed.stdout


def test_a_protocol_level_refusal_carries_a_non_zero_exit(repo):
    """The exit vocabulary has to hold at the protocol boundary, not just below it.

    `_text(error=True)` set `isError` but left `_meta.exit` absent, which clients read
    as 0. So a schema-level rejection — a missing required argument, an unknown one —
    looked identical, to an agent reading exit codes, to a call that worked. That is
    the same class as UNAVAILABLE-read-as-pass, one layer up.
    """
    run_cli(repo, "init")
    proc = _server(repo)
    try:
        _rpc(
            proc,
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "1"},
            },
            rid=1,
        )
        _rpc(proc, "notifications/initialized")
        line = _rpc(
            proc, "tools/call", {"name": "ddflow_bug_fixed", "arguments": {"id": "B1"}}, rid=2
        )
        res = json.loads(line)["result"]
        assert res["isError"], res
        assert res.get("_meta", {}).get("exit") == 1, (
            f"an agent reading only the exit code cannot see this refusal: {res}"
        )
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()

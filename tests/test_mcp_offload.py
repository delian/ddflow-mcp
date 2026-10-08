"""A long tool runs in a worker process, never in the stdio loop itself.

Three failures of one shape, all seen on 2026-10-04 with a sequential loop:

* B5f209cb092: a 534 s `ddflow_gate_run` held the server, and every other call (brief,
  show, gate_status, heartbeat) timed out for the whole run.
* B55e649ca6e: under a heavy reviewer call the server went away and the session never
  regained its tools.
* B9abc247444: a client that gave up on a review left a start marker and no outcome.

Each test drives a REAL server process (`python -m ddflow mcp`) because the property
under test is about processes: what keeps answering, and what survives a kill.
"""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

ROOT = Path(__file__).resolve().parents[1]
DEADLINE_S = 30
pytestmark = pytest.mark.skipif(
    not Path("/proc/self/task").is_dir(), reason="finds the worker through /proc"
)


def _server(repo: Path, stderr=subprocess.DEVNULL) -> subprocess.Popen:
    env = {**os.environ, "PYTHONPATH": str(ROOT), "DDFLOW_AGENT": "offload-test"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "ddflow", "--repo", str(repo), "mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=stderr,
        text=True,
        bufsize=1,
        env=env,
    )
    # Lines through a thread and a queue: a selector on the fd misses a second line
    # that one buffered read already pulled out of the pipe.
    proc.lines = queue.Queue()

    def pump() -> None:
        for line in proc.stdout:
            proc.lines.put(line)
        proc.lines.put("")

    threading.Thread(target=pump, daemon=True).start()
    return proc


def _send(proc, method: str, params: dict | None = None, rid: int | None = None) -> None:
    msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if rid is not None:
        msg["id"] = rid
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()


def _frames(proc, want: set[int], timeout_s: float = DEADLINE_S) -> list[dict]:
    """Replies in arrival order until every id in `want` has answered, or the deadline."""
    got: list[dict] = []
    end = time.monotonic() + timeout_s
    while want - {f.get("id") for f in got}:
        left = end - time.monotonic()
        try:
            line = proc.lines.get(timeout=max(left, 0.001)) if left > 0 else ""
        except queue.Empty:
            break
        if not line:
            break
        frame = json.loads(line)
        if "method" not in frame:
            got.append(frame)
    return got


def _call(name: str, **arguments) -> dict:
    return {"name": name, "arguments": arguments}


def _start(proc) -> None:
    _send(
        proc,
        "initialize",
        {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}},
        rid=1,
    )
    assert _frames(proc, {1}), f"no handshake; exit={proc.poll()}"
    _send(proc, "notifications/initialized")


def _stop(proc) -> None:
    try:
        proc.stdin.close()
    except OSError:
        pass
    try:
        proc.wait(timeout=DEADLINE_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _workers(pid: int) -> list[int]:
    """The server's worker processes: its children running `_worker_main`."""
    kids: list[int] = []
    for task in Path(f"/proc/{pid}/task").iterdir():
        for k in (task / "children").read_text().split():
            try:
                if b"_worker_main" in Path(f"/proc/{k}/cmdline").read_bytes():
                    kids.append(int(k))
            except OSError:
                pass
    return kids


def _held(repo: Path) -> Path:
    """A task another agent holds, so `ddflow_wait` on it is a call that takes its time."""
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--globs", "src/**")
    run_cli(repo, "task", "add", "P1.T1", "--phase", "P1", "--globs", "src/a.py")
    run_cli(repo, "--agent", "holder", "claim", "P1.T1", "--no-worktree")
    return repo


def test_a_long_call_does_not_hold_up_the_calls_behind_it(repo):
    """B5f209cb092: a ping and a status call are answered WHILE a long call runs."""
    _held(repo)
    proc = _server(repo)
    try:
        _start(proc)
        _send(proc, "tools/call", _call("ddflow_wait", item="P1.T1", timeout=20, poll=0.2), 2)
        _send(proc, "ping", rid=3)
        _send(proc, "tools/call", _call("ddflow_status"), 4)
        order = [f["id"] for f in _frames(proc, {3, 4}, timeout_s=15)]
        assert 3 in order and 4 in order, (
            f"the calls behind a long one were not answered while it ran: got {order}"
        )
        assert 2 not in order, "the wait returned early, so this proved nothing"
        run_cli(repo, "--agent", "holder", "release", "P1.T1")
        done = _frames(proc, {2})
        assert [f["id"] for f in done] == [2], f"the long call never answered: {done}"
        assert "isError" not in done[0]["result"] or not done[0]["result"]["isError"]
    finally:
        _stop(proc)


def test_a_long_call_whose_process_dies_leaves_the_server_serving(repo):
    """B55e649ca6e: the heavy process dying (an OOM kill, a crash) costs that one call,
    answered as an error, and not the server: the session keeps its tools."""
    _held(repo)
    proc = _server(repo)
    try:
        _start(proc)
        _send(proc, "tools/call", _call("ddflow_wait", item="P1.T1", timeout=20, poll=0.2), 2)
        end = time.monotonic() + DEADLINE_S
        while not (kids := _workers(proc.pid)) and time.monotonic() < end:
            time.sleep(0.05)
        assert kids, "the long call is running inside the server: nothing else can die for it"
        for k in kids:
            os.kill(k, signal.SIGKILL)
        died = _frames(proc, {2})
        assert died and died[0]["result"]["isError"], f"no answer for the killed call: {died}"
        assert "still up" in died[0]["result"]["content"][0]["text"]
        _send(proc, "tools/list", rid=3)
        _send(proc, "tools/call", _call("ddflow_status"), 4)
        after = {f["id"]: f for f in _frames(proc, {3, 4})}
        assert proc.poll() is None and set(after) == {3, 4}, (
            f"the server did not survive its worker: exit={proc.poll()}, got {sorted(after)}"
        )
        assert after[3]["result"]["tools"]
    finally:
        _stop(proc)


def test_a_client_that_goes_away_does_not_lose_the_run(repo):
    """B9abc247444: the server is killed (a client timeout, a disconnect) while a gate
    runs; the run still finishes and its outcome is recorded, not just its start."""
    run_cli(repo, "init")
    (repo / ".ddflow" / "gates.toml").write_text(
        '[gate.unit_tests]\ncommand = "sleep 2"\ncwd = "repo"\n'
    )
    run_cli(repo, "phase", "add", "P1", "--globs", "src/**")
    run_cli(repo, "task", "add", "P1.T1", "--phase", "P1", "--globs", "src/a.py")
    run_cli(repo, "--agent", "offload-test", "claim", "P1.T1", "--no-worktree")
    # The client's stderr pipe goes with the client: a worker that inherited it would be
    # writing into a broken pipe for the rest of the run.
    proc = _server(repo, stderr=subprocess.PIPE)
    try:
        _start(proc)
        _send(proc, "tools/call", _call("ddflow_gate_run", id="P1.T1", gate="unit_tests"), 2)
        end = time.monotonic() + DEADLINE_S
        while not (kids := _workers(proc.pid)) and time.monotonic() < end:
            time.sleep(0.05)
        assert kids, "the gate is running inside the server, so it dies with it"
        theirs = os.readlink(f"/proc/{proc.pid}/fd/2")
        assert all(os.readlink(f"/proc/{k}/fd/2") != theirs for k in kids), (
            "the worker writes into the client's stderr pipe, which goes with the client"
        )
        proc.stderr.close()
        proc.kill()
        proc.wait()
        end = time.monotonic() + DEADLINE_S
        outcome = ""
        while time.monotonic() < end:
            _rc, shown, _err = run_cli(repo, "--json", "show", "P1.T1")
            outcome = (json.loads(shown).get("gates") or {}).get("unit_tests", {})
            if isinstance(outcome, dict) and outcome.get("outcome"):
                break
            time.sleep(0.25)
        assert isinstance(outcome, dict) and outcome.get("outcome") == "passed", (
            f"the run was lost with its client: unit_tests = {outcome!r}"
        )
    finally:
        _stop(proc)


def _until(check, timeout_s: float = DEADLINE_S):
    """Poll `check()` until it is truthy or the deadline; the last value either way."""
    end = time.monotonic() + timeout_s
    value = check()
    while not value and time.monotonic() < end:
        time.sleep(0.05)
        value = check()
    return value


def test_a_burst_of_long_calls_is_capped_not_all_started(repo):
    """Each worker is a whole interpreter: past `MAX_WORKERS` a call is answered busy.

    B306fd11bc8: nothing here may depend on how fast a worker starts. A fork that has
    not yet exec'd shows the parent's command line, so the worker count is polled to the
    cap, not read once; and the waits outlast any plausible startup under load."""
    from ddflow.surfaces.mcp import MAX_WORKERS

    _held(repo)
    proc = _server(repo)
    try:
        _start(proc)
        ids = list(range(2, 3 + MAX_WORKERS))
        for rid in ids:
            _send(proc, "tools/call", _call("ddflow_wait", item="P1.T1", timeout=300, poll=0.2), rid)
        busy = _frames(proc, {ids[-1]})
        assert [f["id"] for f in busy] == [ids[-1]], busy
        assert busy[0]["result"]["_meta"]["exit"] == 2
        assert _until(lambda: len(_workers(proc.pid)) >= MAX_WORKERS), _workers(proc.pid)
        assert len(_workers(proc.pid)) == MAX_WORKERS
        run_cli(repo, "--agent", "holder", "release", "P1.T1")
        assert {f["id"] for f in _frames(proc, set(ids[:-1]))} == set(ids[:-1])
    finally:
        _stop(proc)


def test_a_tool_name_that_is_not_a_string_does_not_end_the_loop(repo):
    """The offload check reads client input before any per-call error handling."""
    run_cli(repo, "init")
    proc = _server(repo)
    try:
        _start(proc)
        _send(proc, "tools/call", {"name": ["ddflow_review"], "arguments": {}}, 2)
        _send(proc, "ping", rid=3)
        assert {f["id"] for f in _frames(proc, {2, 3})} == {2, 3}, f"exit={proc.poll()}"
    finally:
        _stop(proc)

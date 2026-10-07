"""A timed-out gate or ci command takes its whole process group with it (Bed0f5b6d99).

`run_command_gate` and `ci.run` ran the operator's shell line with `subprocess.run(...,
shell=True, timeout=...)`, which on timeout kills only the shell: the command's children
(a test runner and its workers) kept running orphaned, using CPU and writing into a
worktree that may be removed next. Each test starts a grandchild that records its pid,
times the command out after 1 s, and requires the grandchild gone and the call prompt.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import run_cli

from ddflow.config import Config
from ddflow.infra import proc as P
from ddflow.services import ci as CI
from ddflow.services import gates as G

pytestmark = pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX")


def _command(pidfile: Path) -> str:
    # `sleep 30 & ... wait`: the grandchild is the sleep, not the shell; the shell waits on
    # it, so killing only the shell leaves the sleep holding the pipes and running.
    return f"sleep 30 & echo $! > '{pidfile}'; wait"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie still answers kill(0); it is dead for our purposes. `ps` reads the state
    # on any POSIX system, procfs or not (a missing /proc must not read as "dead").
    p = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False
    )
    state = p.stdout.strip()
    return bool(state) and not state.startswith("Z")


def _gone(pidfile: Path, within_s: float = 3.0) -> bool:
    pid = int(pidfile.read_text().strip())
    deadline = time.monotonic() + within_s
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    os.kill(pid, 9)  # do not leave it behind for the rest of the suite
    return False


def test_a_timed_out_command_gate_kills_its_grandchild(tmp_path):
    pidfile = tmp_path / "child.pid"
    gdef = G.GateDef(id="t", command=_command(pidfile), timeout_s=1)
    start = time.monotonic()
    outcome, evidence = G.run_command_gate(gdef, tmp_path)
    elapsed = time.monotonic() - start
    assert outcome == "unavailable" and evidence["reason"] == "timed out after 1s"
    assert elapsed < 10, f"the call took {elapsed:.1f}s"
    assert _gone(pidfile), "the gate command's child outlived its timeout"


def test_a_timed_out_ticking_gate_kills_its_grandchild(tmp_path):
    pidfile = tmp_path / "child.pid"
    gdef = G.GateDef(id="t", command=_command(pidfile), timeout_s=1)
    ticks = []
    start = time.monotonic()
    outcome, evidence = G.run_command_gate(
        gdef, tmp_path, on_tick=lambda: ticks.append(1), tick_s=0.2
    )
    elapsed = time.monotonic() - start
    assert outcome == "unavailable" and evidence["reason"] == "timed out after 1s"
    assert elapsed < 10, f"the call took {elapsed:.1f}s"
    assert ticks, "the keep-alive never ran"
    assert _gone(pidfile), "the ticking gate command's child outlived its timeout"


def test_a_timed_out_ci_command_kills_its_grandchild(repo, tmp_path):
    run_cli(repo, "init")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "adopt"], check=True)
    pidfile = tmp_path / "child.pid"
    cfg = Config.load(repo)
    cfg.ci.timeout_s = 1
    start = time.monotonic()
    res = CI.run(repo, cfg, base="HEAD", command=_command(pidfile))
    elapsed = time.monotonic() - start
    assert res.status == "unavailable" and "timed out after 1s" in res.reason
    assert elapsed < 20, f"the call took {elapsed:.1f}s"
    assert _gone(pidfile), "the ci command's child outlived its timeout"


def test_run_shell_returns_the_exit_and_both_streams_when_the_command_finishes(tmp_path):
    r = P.run_shell("echo out; echo err >&2; exit 3", cwd=tmp_path, timeout_s=10)
    assert (r.code, r.out, r.err) == (3, "out\n", "err\n")
    assert r.finished and not r.ok and not r.timed_out and not r.could_not_run


def test_run_shell_reports_a_timeout_after_killing_the_group(tmp_path):
    pidfile = tmp_path / "child.pid"
    r = P.run_shell(f"echo partial; {_command(pidfile)}", cwd=tmp_path, timeout_s=1)
    assert r.timed_out and r.code is None and not r.could_not_run
    assert r.out == "partial\n", "the output written before the timeout is kept"
    assert _gone(pidfile)


def test_run_shell_reports_a_command_that_could_not_start(tmp_path):
    r = P.run_shell("true", cwd=tmp_path / "missing", timeout_s=10)
    assert r.could_not_run and r.code is None and not r.timed_out and r.err


def test_run_shell_can_fold_stderr_into_stdout(tmp_path):
    r = P.run_shell("echo a; echo b >&2; echo c", cwd=tmp_path, timeout_s=10, merge_stderr=True)
    assert r.out == "a\nb\nc\n" and r.err == ""


def test_run_shell_keeps_stdin_detached():
    """Run from a parent whose stdin is a REAL pipe with bytes on it (as an MCP server's
    is): inside pytest fd 0 is already empty, and the check would pass either way
    (see test_stdio_safety.test_the_guard_actually_detaches_stdin)."""
    root = Path(__file__).resolve().parents[1]
    parent = (
        f"import sys; sys.path.insert(0, {str(root)!r});"
        "from ddflow.infra import proc as P;"
        "r = P.run_shell('cat; echo done', timeout_s=30);"
        "print('CHILD_SAW=' + repr(r.out));"
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
    assert "CHILD_SAW='done\\n'" in p.stdout, p.stdout
    assert "PARENT_KEPT='PROTOCOL-BYTES\\n'" in p.stdout, p.stdout


def test_run_shell_refuses_a_tick_that_would_never_fire(tmp_path):
    with pytest.raises(ValueError):
        P.run_shell("true", cwd=tmp_path, timeout_s=10, on_tick=lambda: None)

"""`ddflow_identify` names the agent for its shell commands too (Bfad021e8d9).

Observed: after identifying the MCP connection as 'kilo-onboard', `ddflow claim` run
through the shell recorded the lease under the tree-derived name; `ddflow_heartbeat` on
the connection then answered "no lease held" and the commit hook refused the agent's
own files. The server's own advice -- identify first, then claim -- split one agent's
work across two identities.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.surfaces.mcp import Server

ROOT = Path(__file__).resolve().parents[1]
needs_proc = pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="reads /proc")


def _shell(repo: Path, *argv: str, **env: str) -> subprocess.CompletedProcess:
    """The CLI as the agent's shell runs it: no --agent, and no DDFLOW_AGENT unless given."""
    base = {k: v for k, v in os.environ.items() if k != "DDFLOW_AGENT"}
    return subprocess.run(
        [sys.executable, "-m", "ddflow", "--repo", str(repo), *argv],
        capture_output=True,
        text=True,
        env={**base, "PYTHONPATH": str(ROOT), **env},
        timeout=120,
    )


def _call(srv: Server, name: str, **arguments) -> dict:
    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call"}
    msg["params"] = {"name": name, "arguments": arguments}
    return srv.handle(msg)["result"]


def _holder(repo: Path) -> str:
    _rc, out, _err = run_cli(repo, "--json", "show", "P1.T1")
    return (json.loads(out).get("lease") or {}).get("holder", "")


@pytest.fixture
def connection(repo):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--globs", "src/**")
    run_cli(repo, "task", "add", "P1.T1", "--phase", "P1", "--globs", "src/a.py")
    srv = Server(repo)
    _call(srv, "ddflow_identify", agent="kilo-onboard")
    yield repo, srv
    _call(srv, "ddflow_identify", agent="")


@needs_proc
def test_a_shell_claim_after_identify_is_the_identified_agents(connection):
    repo, srv = connection
    r = _shell(repo, "claim", "P1.T1", "--no-worktree")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _holder(repo) == "kilo-onboard", "the shell claimed under a derived name"
    beat = _call(srv, "ddflow_heartbeat", id="P1.T1")
    assert not beat.get("isError"), beat["content"][0]["text"]


@needs_proc
def test_an_explicit_shell_identity_still_wins(connection):
    repo, _srv = connection
    r = _shell(repo, "claim", "P1.T1", "--no-worktree", DDFLOW_AGENT="subagent-x")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _holder(repo) == "subagent-x"


@needs_proc
def test_withdrawing_the_declaration_returns_the_shell_to_its_derived_name(connection):
    repo, srv = connection
    _call(srv, "ddflow_identify", agent="")
    r = _shell(repo, "claim", "P1.T1", "--no-worktree")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _holder(repo) not in ("", "kilo-onboard")


@needs_proc
def test_identify_says_whether_the_shell_will_see_it(connection):
    _repo, srv = connection
    text = _call(srv, "ddflow_identify", agent="kilo-onboard")["content"][0]["text"]
    assert "and for its shell" in text, text


@needs_proc
def test_a_record_for_a_process_that_has_exited_is_neither_found_nor_kept(repo):
    """Keyed by pid AND start time: a reused pid is a different process."""
    from ddflow.infra import harness_identity as H

    d = repo / ".git" / H.DIR
    d.mkdir()
    me = H._stat(os.getppid())
    if me is None:
        pytest.skip("reads /proc")
    stale = d / f"{os.getppid()}-{int(me[2]) + 1}"
    stale.write_text("ghost\n")
    assert H.declared(repo) == ""
    assert H.declare(repo, "live") == ""
    assert not stale.exists()
    assert H.declared(repo) == "live"
    H.declare(repo, "")
    assert H.declared(repo) == ""


@needs_proc
def test_the_record_is_shared_with_a_linked_worktree(repo):
    from ddflow.infra import harness_identity as H

    wt = repo.parent / "wt"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(wt)], check=True)
    try:
        assert H.declare(wt, "kilo-wt") == ""
        assert H.declared(repo) == "kilo-wt"
        assert H.declared(wt) == "kilo-wt"
    finally:
        H.declare(repo, "")


def test_identify_says_so_when_the_shell_cannot_see_it(repo, monkeypatch):
    """A declaration the shell will not see is reported, not passed off as done."""
    from ddflow.infra import harness_identity as H

    run_cli(repo, "init")
    monkeypatch.setattr(H, "_harness", lambda pid: [])
    srv = Server(repo)
    text = _call(srv, "ddflow_identify", agent="kilo-onboard")["content"][0]["text"]
    assert "pass --agent" in text, text


@needs_proc
def test_a_record_whose_process_cannot_be_read_is_kept(repo, monkeypatch):
    """Unsure is not gone: only a definitely-exited harness's record is pruned."""
    from ddflow.infra import harness_identity as H

    d = repo / ".git" / H.DIR
    d.mkdir()
    kept = d / f"{os.getpid()}-1"
    kept.write_text("other\n")
    real = H._stat
    monkeypatch.setattr(H, "_stat", lambda pid: None if pid == os.getpid() else real(pid))
    H.declare(repo, "live")
    assert kept.exists()
    H.declare(repo, "")


def test_a_separate_git_dir_resolves_to_itself(tmp_path):
    from ddflow.infra import harness_identity as H

    gd = tmp_path / "store"
    wt = tmp_path / "wt"
    subprocess.run(["git", "init", "-q", "--separate-git-dir", str(gd), str(wt)], check=True)
    assert H._dir(wt) == gd.resolve() / H.DIR


@needs_proc
def test_a_restarted_server_under_the_same_harness_keeps_the_name(connection):
    """Else the new connection derives a name while the shell keeps the declared one."""
    repo, _srv = connection
    assert Server(repo).agent == "kilo-onboard"


def test_the_record_does_not_climb_past_a_shell(monkeypatch):
    """An interactive shell may be the harness, or the terminal above it."""
    from ddflow.infra import harness_identity as H

    chain = {40: ("uv", 30, "4"), 30: ("bash", 20, "3"), 20: ("tmux", 1, "2")}
    monkeypatch.setattr(H, "_stat", chain.get)
    assert H._harness(40) == ["40-4", "30-3"]

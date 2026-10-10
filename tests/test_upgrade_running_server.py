"""A long-lived MCP server survives a self-upgrade without writing as the old code
(B-arch-upgrade-risks, risk 3; decisions D-self-upgrade and D-upgrade-skew-guard).

An MCP server is a process that outlives the install it was started from. `ddflow upgrade
--self` replaces the package on disk, the NEW code (a CLI run, another agent's server) stamps
the project's log with its version, and the old server keeps answering with the old code. What
protects the log is the skew guard: the old code compares its own version with the log's
stamps, so it must refuse to write once the new code has been there, and say to restart. This
starts a real server (the old code: `ddflow.__version__` set low in this process), runs the new
code as a separate process, and proves

- the old server still READS, and says `restart the server` (once) as soon as it looks;
- its writes are refused (exit 3, the version to upgrade to) and leave every byte of the log
  as the new code left it;
- a server started afterwards (the restart) writes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import run_cli

import ddflow
from ddflow.services import upgrade_notice as UN
from ddflow.surfaces.mcp import Server

ROOT = Path(__file__).resolve().parents[1]
OLD = "0.0.5"
REFUSED = 3  # the exit code of a refusal


def call(server: Server, name: str, arguments: dict | None = None, at: int = 1) -> dict:
    reply = server._dispatch(
        {
            "jsonrpc": "2.0",
            "id": at,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }
    )
    return reply["result"]


def text(result: dict) -> str:
    return "\n".join(c["text"] for c in result["content"])


def shards(repo: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted((repo / ".ddflow" / "events").glob("*.jsonl"))}


def new_code(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """The upgraded ddflow, as a separate process (its own version, not the patched one)."""
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("DDFLOW_")},
        "PYTHONPATH": str(ROOT),
        "PYTHONSAFEPATH": "1",
        "DDFLOW_NO_UPDATE_CHECK": "1",
    }
    return subprocess.run(
        [sys.executable, "-m", "ddflow", "--repo", str(repo), "--agent", "newcode", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=repo,
        timeout=300,
    )


@pytest.fixture
def project(repo: Path) -> Path:
    assert run_cli(repo, "init")[0] == 0
    return repo


def test_a_server_that_outlived_a_self_upgrade_refuses_writes_and_says_restart(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    new_version = ddflow_new_version()
    # the server is started by the OLD code
    monkeypatch.setattr(ddflow, "__version__", OLD)
    server = Server(project)
    assert not call(server, "ddflow_status").get("isError")
    first = call(server, "ddflow_phase_add", {"id": "P1", "title": "before the upgrade"}, at=2)
    assert not first.get("isError"), text(first)

    # `ddflow upgrade --self` replaces the package on disk, and the new code writes the log
    monkeypatch.setattr(UN, "installed_version", lambda: new_version)
    assert new_code(project, "phase", "add", "P2", "--title", "from the new code").returncode == 0
    left_by_the_new_code = shards(project)

    # the old server still reads, and (a minute on) says to restart -- once
    server._stale_checked_at = float("-inf")
    read = text(call(server, "ddflow_status", at=3))
    assert "restart the server" in read and new_version in read
    assert "restart the server" not in text(call(server, "ddflow_status", at=4))

    # its writes are refused with the remedy, and the log is exactly as the new code left it
    refused = call(server, "ddflow_phase_add", {"id": "P3", "title": "from the old server"}, at=5)
    assert refused["_meta"]["exit"] == REFUSED, text(refused)
    body = text(refused)
    assert body.startswith("REFUSED:") and f"ddflow >= {new_version}" in body
    assert "restart the MCP server" in body and "Reads still work" in body
    assert shards(project) == left_by_the_new_code

    # the restart: a server of the new code writes
    monkeypatch.undo()
    fresh = Server(project)
    done = call(fresh, "ddflow_phase_add", {"id": "P3", "title": "after the restart"}, at=6)
    assert done["_meta"].get("exit", 0) == 0 and not done.get("isError"), text(done)
    assert any(b"P3" in raw for raw in shards(project).values())


def ddflow_new_version() -> str:
    """The version of the code the separate process runs (the real one, not the patched)."""
    out = subprocess.run(
        [sys.executable, "-c", "import ddflow; print(ddflow.__version__)"],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT), "PYTHONSAFEPATH": "1"},
        timeout=60,
    )
    return out.stdout.strip()


def test_the_refusal_names_the_version_to_upgrade_to(project: Path, monkeypatch) -> None:
    assert new_code(project, "phase", "add", "P2", "--title", "x").returncode == 0
    monkeypatch.setattr(ddflow, "__version__", OLD)
    server = Server(project)
    result = call(server, "ddflow_phase_add", {"id": "P3", "title": "y"})
    assert result["_meta"]["exit"] == REFUSED
    assert f"ddflow >= {ddflow_new_version()} is needed" in json.dumps(result)

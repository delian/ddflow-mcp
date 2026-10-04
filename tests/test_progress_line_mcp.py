"""MCP replies carry the optional extras the CLI shows: the progress block after a
completion and the base's health after a merge (B-hy-progress-mcp)."""

from __future__ import annotations

import json
import os
import stat
import subprocess

from conftest import run_cli

from ddflow.surfaces.mcp import Server


def _call(repo, name, args):
    reply = Server(repo).handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": name, "arguments": args}}
    )  # fmt: skip
    return json.loads(reply["result"]["content"][0]["text"])


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def test_mcp_complete_carries_the_progress_block(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "a.py")
    body = _call(repo, "ddflow_complete", {"id": "T1", "force": True})
    assert body["progress"].startswith("Progress: tasks 1/1 (100%)")


def test_mcp_complete_has_no_progress_key_when_it_is_off(repo):
    run_cli(repo, "init")
    run_cli(repo, "config", "session.progress_after_complete", "off")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "a.py")
    assert "progress" not in _call(repo, "ddflow_complete", {"id": "T1", "force": True})


def test_mcp_merge_carries_the_base_health(repo, tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    pc = bin_dir / "pre-commit"
    pc.write_text("#!/bin/sh\necho 'ruff check.....................Passed'\n")
    pc.chmod(pc.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    run_cli(repo, "init")
    (repo / ".pre-commit-config.yaml").write_text("repos: []\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "adopt")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "w.txt")
    assert run_cli(repo, "claim", "T1")[0] == 0
    wt = (repo / ".ddflow" / "worktrees" / "T1").resolve()
    (wt / "w.txt").write_text("w\n")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-qm", "work")
    body = _call(repo, "ddflow_merge", {"id": "T1"})
    assert body["ci"]["status"] == "passed", body

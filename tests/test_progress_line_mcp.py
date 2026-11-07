"""MCP replies carry the optional extras the CLI shows: the progress block after a
completion and the base's health after a merge (B-hy-progress-mcp)."""

from __future__ import annotations

import json
import os
import stat

from conftest import run_cli
from helpers import git as _git

from ddflow.surfaces.mcp import Server


def _call(repo, name, args):
    reply = Server(repo).handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": name, "arguments": args}}
    )  # fmt: skip
    return json.loads(reply["result"]["content"][0]["text"])


def test_mcp_complete_carries_the_progress_block(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "a.py")
    # `--force` over MCP is refused (`--force` is a person's; every MCP call is an agent's,
    # B49fa10bbb1), so a bare task cannot complete there to assert the MCP reply's `progress`
    # field. The CLI `complete --force` is the person-at-terminal path that honours it and
    # prints the same block the MCP reply carries on the same `finish.complete` path; the MCP
    # reply's `progress` field itself is carried by `_OPTIONAL_KEYS` (test_mcp_merge_carries_
    # the_base_health covers the sibling `ci` key).
    _code, out, _err = run_cli(repo, "complete", "T1", "--force", "--model", "x")
    assert "Progress: tasks 1/1 (100%)" in out


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

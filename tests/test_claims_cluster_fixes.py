"""Bc1fe69741f: `ddflow mcp` passes the caller's cwd as called_from.
Bf37fe4fce3: `ddflow block` refuses a DONE item unless --reopen.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.infra.log import EventLog

OK, REFUSED = 0, 3


def test_cli_mcp_serves_with_the_callers_cwd(repo, tmp_path, monkeypatch):
    import ddflow.surfaces.mcp as M
    from ddflow.surfaces.cli import cmd_mcp

    seen = {}
    monkeypatch.setattr(M, "serve", lambda r, *a, **k: seen.update(repo=r, **k))
    where = tmp_path / "elsewhere"
    where.mkdir()
    monkeypatch.chdir(where)
    cmd_mcp(SimpleNamespace(), SimpleNamespace(repo=repo, _start=where))
    assert seen["repo"] == repo
    assert seen.get("called_from") == where


def _done_item(repo: Path) -> None:
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t")
    EventLog(repo, "x").append("item.completed", "T1", {"kind": "task"})


def _state(repo: Path) -> str:
    return next(
        ln.split()[1]
        for ln in run_cli(repo, "show", "T1")[1].splitlines()
        if ln.strip().startswith("state")
    )


def test_block_refuses_a_done_item(repo):
    _done_item(repo)
    code, out, err = run_cli(repo, "block", "T1", "--reason", "typo")
    assert code == REFUSED, (code, out, err)
    assert "DONE" in out + err and "--reopen" in out + err
    assert _state(repo) == "done"


def test_block_reopen_is_the_explicit_way(repo):
    _done_item(repo)
    code, out, err = run_cli(repo, "block", "T1", "--reason", "data fix", "--reopen")
    assert code == OK, (code, out, err)
    assert _state(repo) == "blocked"


def test_block_still_blocks_an_open_item(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t")
    assert run_cli(repo, "block", "T1", "--reason", "wait")[0] == OK


def test_block_over_mcp_needs_reopen_for_a_done_item(repo):
    from ddflow.surfaces.mcp import Server

    _done_item(repo)

    def call(**extra):
        msg = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_block", "arguments": {"id": "T1", "reason": "x", **extra}},
        }
        return Server(repo).handle(msg)["result"]

    refused = call()
    assert refused["_meta"]["exit"] == REFUSED
    assert _state(repo) == "done"
    assert call(reopen=True)["_meta"]["exit"] == OK
    assert _state(repo) == "blocked"

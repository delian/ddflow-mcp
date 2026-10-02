"""B9fdd35d104: `ddflow block` refuses an ABANDONED item unless --reopen."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

OK, REFUSED = 0, 3


def _abandoned(repo: Path) -> None:
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t")
    assert run_cli(repo, "abandon", "T1", "--reason", "dropped")[0] == OK


def _state(repo: Path) -> str:
    return next(
        ln.split()[1]
        for ln in run_cli(repo, "show", "T1")[1].splitlines()
        if ln.strip().startswith("state")
    )


def test_block_refuses_an_abandoned_item(repo):
    _abandoned(repo)
    code, out, err = run_cli(repo, "block", "T1", "--reason", "typo")
    assert code == REFUSED, (code, out, err)
    text = out + err
    assert "T1" in text and "ABANDONED" in text and "--reopen" in text
    assert _state(repo) == "abandoned"


def test_block_reopen_revives_an_abandoned_item(repo):
    _abandoned(repo)
    code, out, err = run_cli(repo, "block", "T1", "--reason", "revive", "--reopen")
    assert code == OK, (code, out, err)
    assert _state(repo) == "blocked"


def test_block_abandoned_over_mcp_needs_reopen(repo):
    from ddflow.surfaces.mcp import Server

    _abandoned(repo)

    def call(**extra):
        msg = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_block", "arguments": {"id": "T1", "reason": "x", **extra}},
        }
        return Server(repo).handle(msg)["result"]

    assert call()["_meta"]["exit"] == REFUSED
    assert _state(repo) == "abandoned"
    assert call(reopen=True)["_meta"]["exit"] == OK
    assert _state(repo) == "blocked"

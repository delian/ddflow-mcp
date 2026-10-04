"""`bug reopen` over MCP (B451aafa44d): an MCP-only agent that closed a bug by mistake
can undo it, as `ddflow_bug_invalid` with `reopen=true` (no new tool: the tools/list
byte budget is nearly full)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.surfaces.mcp import Server


def _call(srv: Server, **arguments) -> dict:
    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call"}
    msg["params"] = {"name": "ddflow_bug_invalid", "arguments": arguments}
    return srv.handle(msg)["result"]


def _bug(repo: Path, bid: str) -> dict:
    _rc, out, _err = run_cli(repo, "--json", "show", bid)
    return json.loads(out)


def test_a_bug_closed_by_mistake_is_reopened_over_mcp(repo):
    run_cli(repo, "init")
    _rc, out, _err = run_cli(
        repo, "--json", "bug", "found", "--summary", "it breaks", "--severity", "low", "--new"
    )
    bid = json.loads(out)["id"]
    srv = Server(repo)
    closed = _call(srv, id=bid, reason="thought it was noise")
    assert not closed.get("isError"), closed
    reopened = _call(srv, id=bid, reason="it was real after all", reopen=True)
    assert not reopened.get("isError"), reopened
    assert reopened["_meta"]["exit"] == 0, reopened
    body = json.loads(reopened["content"][0]["text"])
    assert body["was"] == "invalid" and body["reason_given"] == "it was real after all"
    assert _bug(repo, bid)["state"] == "open"


def test_reopening_an_open_bug_is_refused_not_silently_done(repo):
    run_cli(repo, "init")
    _rc, out, _err = run_cli(
        repo, "--json", "bug", "found", "--summary", "still open", "--severity", "low", "--new"
    )
    bid = json.loads(out)["id"]
    r = _call(Server(repo), id=bid, reason="why not", reopen=True)
    assert r["_meta"]["exit"] == 3, r

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


def test_a_bug_closed_as_fixed_is_reopened_too(repo):
    run_cli(repo, "init")
    _rc, out, _err = run_cli(
        repo, "--json", "bug", "found", "--summary", "flaky", "--severity", "low", "--new"
    )
    bid = json.loads(out)["id"]
    (repo / "tests").mkdir()
    (repo / "tests" / "test_x.py").write_text("def test_y():\n    pass\n")
    rc, out, err = run_cli(
        repo, "bug", "fixed", bid, "--regression-test", "tests/test_x.py::test_y"
    )
    assert rc == 0, out + err
    r = _call(Server(repo), id=bid, reason="the fix did not hold", reopen=True)
    assert r["_meta"]["exit"] == 0, r
    body = json.loads(r["content"][0]["text"])
    assert body["was"] == "fixed" and "next" in body and "fix_task" in body
    assert _bug(repo, bid)["state"] == "open"


def test_the_mode_is_strict_and_nothing_is_dropped(repo):
    run_cli(repo, "init")
    _rc, out, _err = run_cli(
        repo, "--json", "bug", "found", "--summary", "x", "--severity", "low", "--new"
    )
    bid = json.loads(out)["id"]
    srv = Server(repo)
    _call(srv, id=bid, reason="noise")
    malformed = _call(srv, id=bid, reason="real", reopen="false")
    assert malformed.get("isError") and "reopen must be" in malformed["content"][0]["text"]
    conflict = _call(srv, id=bid, reason="real", reopen=True, evidence="probe")
    assert conflict["_meta"]["exit"] == 3, conflict
    blank = _call(srv, id=bid, reason="", reopen=True)
    assert blank.get("isError") and "reason" in blank["content"][0]["text"], blank
    assert _bug(repo, bid)["state"] == "invalid"

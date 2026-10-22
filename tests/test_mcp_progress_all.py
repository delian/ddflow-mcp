"""Every offloaded MCP tool streams `notifications/progress` while it runs, even silent
(B7b3b4cf483): Claude Code aborts a tool call that sends nothing for its idle window."""

from __future__ import annotations

import time

import pytest

from ddflow.core.outcome import Outcome
from ddflow.surfaces import mcp
from ddflow.surfaces.tools import TOOLS


@pytest.mark.parametrize("name", sorted(mcp.OFFLOADED))
def test_a_silent_offloaded_tool_still_sends_progress(repo, monkeypatch, name):
    (repo / ".ddflow").mkdir(exist_ok=True)
    (repo / ".ddflow" / "config.toml").write_text("[mcp]\nprogress_s = 0.3\n")
    spec = dict(TOOLS[name])

    def silent(repo_, args, agent, **_kw):  # no on_progress: a gate command with no output
        time.sleep(1.3)
        return Outcome(kind="status", data={"text": "ok"})

    spec["api"] = silent
    monkeypatch.setitem(TOOLS, name, spec)
    needs = {k: "x" for k, v in TOOLS[name]["properties"].items() if v[2]}
    srv = mcp.Server(repo, called_from=repo)
    sent: list[dict] = []
    srv.notify = sent.append
    reply = srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": needs, "_meta": {"progressToken": "tok"}},
        }
    )
    beats = [f for f in sent if f.get("method") == "notifications/progress"]
    assert len(beats) >= 2, f"{name} sent {len(beats)} progress frames in 1.3 s: {reply}"
    assert all(f["params"]["progressToken"] == "tok" for f in beats)
    got = [f["params"]["progress"] for f in beats]
    assert got == sorted(set(got)), "progress must strictly increase"


def test_no_token_no_heartbeat(repo, monkeypatch):
    (repo / ".ddflow").mkdir(exist_ok=True)
    (repo / ".ddflow" / "config.toml").write_text("[mcp]\nprogress_s = 0.3\n")
    spec = dict(TOOLS["ddflow_wait"])
    spec["api"] = lambda *a, **k: (time.sleep(0.8), Outcome(kind="status", data={}))[1]
    monkeypatch.setitem(TOOLS, "ddflow_wait", spec)
    srv = mcp.Server(repo, called_from=repo)
    sent: list[dict] = []
    srv.notify = sent.append
    srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_wait", "arguments": {}},
        }
    )
    assert sent == []

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


def test_progress_from_two_threads_reaches_the_client_in_order(repo):
    """A heartbeat thread and a tool's own lines share one counter: count and send are
    one step, or a slow first send lets a later number overtake it."""
    import threading

    srv = mcp.Server(repo, called_from=repo)
    sent: list[int] = []

    def notify(frame: dict) -> None:
        n = frame["params"]["progress"]
        if n == 1:
            time.sleep(0.2)  # the first sender is held up while the second one counts
        sent.append(n)

    srv.notify = notify
    say = srv._progress({"_meta": {"progressToken": "t"}})
    ts = [threading.Thread(target=say, args=("x",)) for _ in range(2)]
    ts[0].start()
    time.sleep(0.05)
    ts[1].start()
    for t in ts:
        t.join()
    assert sent == [1, 2]


@pytest.mark.parametrize("every", [float("nan"), float("inf"), 0, -1])
def test_a_bad_interval_starts_no_heartbeat(repo, every):
    from ddflow.surfaces.mcp_heartbeat import heartbeat

    said: list[str] = []
    with heartbeat(repo, "t", said.append, every=every):
        time.sleep(0.2)
    assert said == []


@pytest.mark.parametrize("bad", [float("inf"), float("nan"), -1, True, "20"])
def test_the_knob_refuses_a_value_the_heartbeat_cannot_use(bad):
    from ddflow.config import _KNOB_CHECKS

    check = _KNOB_CHECKS["mcp.progress_s"]
    assert check(bad)
    assert not check(0) and not check(20.0)

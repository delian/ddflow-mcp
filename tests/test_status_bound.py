"""MCP `ddflow_status` is bounded (bug Bd6aa9ffde9).

On a 4831-task repository it returned 721,813 characters -- `completed_tasks` listed
every finished task -- past what the harness accepts as one tool result, so the answer
was spilled to a file. The counts stay exact; the lists are cut, and say so.
"""

from __future__ import annotations

import json

from conftest import run_cli

from ddflow.infra.log import EventLog

DONE = 300


def _big_queue(repo) -> None:
    run_cli(repo, "init")
    log = EventLog(repo, "agent-test")
    for i in range(DONE):
        tid = f"T{i:03d}"
        log.append("task.added", tid, {"title": f"finished task number {i} " + "x" * 80})
        log.append("item.completed", tid, {"sha": f"{i:040x}"})
    for i in range(40):
        log.append("task.added", f"R{i:02d}", {"title": f"ready {i}", "globs": [f"r{i}"]})


def _mcp_status(repo) -> str:
    from ddflow.surfaces.mcp import Server

    r = Server(repo, agent="agent-test").handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_status", "arguments": {}},
        }
    )["result"]
    assert not r.get("isError"), r
    return r["content"][0]["text"]


def test_mcp_status_cuts_its_lists_and_keeps_the_counts(repo):
    _big_queue(repo)
    text = _mcp_status(repo)
    body = json.loads(text)
    assert body["tasks"]["done"] == DONE and body["tasks"]["total"] == DONE + 40
    assert len(body["completed_tasks"]) == 25
    assert body["completed_tasks"][-1]["id"] == f"T{DONE - 1:03d}", "the most recent are kept"
    assert body["truncated"]["lists"]["completed_tasks"] == DONE
    assert "ddflow --json status" in body["truncated"]["all"]
    assert len(text) < 20_000, len(text)


def test_the_cli_still_lists_everything(repo):
    _big_queue(repo)
    code, out, err = run_cli(repo, "--json", "status")
    assert code == 0, err
    body = json.loads(out)
    assert len(body["completed_tasks"]) == DONE and "truncated" not in body


def test_a_small_project_is_not_marked_truncated(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    body = json.loads(_mcp_status(repo))
    assert "truncated" not in body and [t["id"] for t in body["ready_now"]] == ["T1"]

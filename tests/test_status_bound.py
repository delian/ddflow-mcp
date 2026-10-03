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
        log.append("task.added", f"R{i:02d}", {"title": f"ready {i}", "globs": [f"r{i:02d}/f.py"]})


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


def test_the_cli_lists_everything_and_differs_from_mcp_only_in_the_cut_lists(repo):
    """The deliberate exception to CLI/MCP byte-identity (roborev job 962): pinned on a
    queue big enough to cross the bound, which the parity test's fixture never is."""
    _big_queue(repo)
    code, out, err = run_cli(repo, "--json", "status")
    assert code == 0, err
    cli = json.loads(out)
    assert len(cli["completed_tasks"]) == DONE and "truncated" not in cli
    assert [t["id"] for t in cli["completed_tasks"]][-1] == f"T{DONE - 1:03d}"
    mcp = json.loads(_mcp_status(repo))
    # 40 ready, 4 offered under max_parallel_tasks = 4, 36 held by the cap.
    assert mcp["truncated"]["lists"] == {"completed_tasks": DONE, "held_by_cap": 36}
    assert mcp["completed_tasks"] == cli["completed_tasks"][-25:]
    assert mcp["held_by_cap"] == cli["held_by_cap"][:25]
    same = {k for k in cli if k not in ("completed_tasks", "held_by_cap")}
    assert {k: mcp[k] for k in same} == {k: cli[k] for k in same}


def test_interrupted_notes_are_cut_too(repo):
    """roborev job 962: `interrupted` describes the same items `in_flight` does and was
    left unbounded."""
    import time

    run_cli(repo, "init")
    log = EventLog(repo, "ghost")
    for i in range(30):
        log.append("task.added", f"I{i:02d}", {"globs": [f"i{i}"]})
        log.append(
            "lease.acquired", f"I{i:02d}", {"holder": "ghost", "at": time.time() - 9e4, "ttl_s": 60}
        )
        log.append("item.started", f"I{i:02d}", {})
    body = json.loads(_mcp_status(repo))
    assert len(body["interrupted"]) == 25 and body["truncated"]["lists"]["interrupted"] == 30


def test_a_small_project_is_not_marked_truncated(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    body = json.loads(_mcp_status(repo))
    assert "truncated" not in body and [t["id"] for t in body["ready_now"]] == ["T1"]

"""Every report prints the same queue totals, and says what it counts (B6341fe42ca).

On the filing of the bug `ddflow status` printed "12/5065 tasks complete" while
`ddflow --json status` said 2222 tasks done: the output caps cut the prose renderer's
own list of done tasks to the row limit, and the headline took `len()` of the cut list.
The fix is one function (`core.progress.totals`) that owns the task / phase / bug totals;
the prose, the JSON, the MCP tool, the completion `Progress:` block and the STATUS.md
export read it, and the screen states the definition with every bucket shown.
"""

from __future__ import annotations

import json
import re

import pytest
from conftest import run_cli

from ddflow.infra.log import EventLog

DONE = 15  # more than the 12-row cut the real queue's budget leaves
ABANDONED = 2
FIX = 2  # live fix tasks (one of them done)
BLOCKED = 2


def _fixture(repo) -> None:
    """15 done tasks, 2 abandoned, 2 fix tasks (1 done), blocked, ready tasks, 1 open bug."""
    run_cli(repo, "init")
    log = EventLog(repo, "agent-test")
    log.append("phase.added", "P1", {"title": "phase one"})
    log.append("phase.added", "P2", {"title": "phase two"})
    for i in range(DONE):
        tid = f"D{i:02d}"
        log.append(
            "task.added", tid, {"title": f"done {i}", "parent": "P1", "globs": [f"{tid}.py"]}
        )
        log.append("item.started", tid, {})
        log.append("item.completed", tid, {"sha": f"sha{i:02d}" * 4})
    for i in range(ABANDONED):
        tid = f"A{i}"
        log.append(
            "task.added", tid, {"title": f"abandoned {i}", "parent": "P1", "globs": [f"{tid}.py"]}
        )
        log.append("item.abandoned", tid, {"reason": "not needed"})
    log.append(
        "task.added",
        "F1",
        {"title": "fix done", "parent": "P1", "fixes": ["Bx"], "globs": ["F1.py"]},
    )
    log.append("item.started", "F1", {})
    log.append("item.completed", "F1", {"sha": "f" * 12})
    log.append(
        "task.added",
        "F2",
        {"title": "fix open", "parent": "P1", "fixes": ["Bx"], "globs": ["F2.py"]},
    )
    log.append("task.added", "R1", {"title": "ready", "parent": "P2", "globs": ["R1.py"]})
    log.append("task.added", "R2", {"title": "ready too", "parent": "P2", "globs": ["R2.py"]})
    for i in range(BLOCKED):
        log.append(
            "task.added",
            f"K{i}",
            {"title": f"blocked {i}", "parent": "P2", "needs": ["R1"], "globs": [f"K{i}.py"]},
        )


@pytest.fixture
def project(repo, monkeypatch):
    monkeypatch.setenv("DDFLOW_OUTPUT_MAX_ROWS", "12")  # the cut that hid the 15 done rows
    _fixture(repo)
    return repo


def _json(repo, *args):
    code, out, err = run_cli(repo, "--json", "status", *args)
    assert code == 0, err
    return json.loads(out)


def test_headline_counts_every_done_task_not_the_rows_it_prints(project):
    """The 12-of-N regression: the bounded prose must not take `len()` of its cut list."""
    code, out, err = run_cli(project, "status")
    assert code == 0, err
    # 23 records: 15 done + 2 abandoned + 2 fix (one done) + 4 waiting; live = 23 - 2 - 2.
    assert "15 of 19 live tasks done" in out
    headline = out.split("\n")[2]  # the headline line
    assert headline.startswith("15 of 19 live tasks done")
    assert "tasks complete" not in headline
    assert "(2 abandoned and 2 fix tasks listed separately)" in headline
    assert "last 12 of 16" in out  # 15 done tasks + the done fix task; the list says it is bounded


def test_status_says_what_it_counts_with_every_bucket(project):
    code, out, err = run_cli(project, "status")
    assert code == 0, err
    t = _json(project)["tasks"]
    assert (
        f"{t['live_done']} of {t['live']} live tasks done "
        f"({t['abandoned']} abandoned and {t['fix']} fix tasks listed separately)"
    ) in out
    for word in ("done", "running", "ready", "held by cap", "blocked", "review", "abandoned"):
        assert re.search(rf"\d+ {word}\b", out), word
    assert f"= {t['total']}" in out  # the buckets are shown summing to the total


def test_buckets_sum_to_total_on_the_json(project):
    t = _json(project)["tasks"]
    parts = ("done", "running", "ready", "held_by_cap", "blocked", "review", "abandoned")
    assert sum(t[k] for k in parts) == t["total"] == 23
    assert t["done"] == DONE + 1 and t["abandoned"] == ABANDONED
    assert t["fix"] == FIX and t["fix_done"] == 1
    assert t["live"] == t["total"] - t["abandoned"] - t["fix"]
    assert t["live_done"] == t["done"] - t["fix_done"]


def test_every_surface_prints_the_same_numbers(project):
    t = _json(project)["tasks"]
    ph = _json(project)["phases"]
    # prose
    _c, prose, _e = run_cli(project, "status")
    assert f"{t['live_done']} of {t['live']} live tasks done" in prose
    # the MCP tool
    from ddflow.surfaces.mcp import Server

    reply = Server(project, agent="agent-test").handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_status", "arguments": {}},
        }
    )["result"]["content"][0]["text"]
    mcp = json.loads(reply)
    assert mcp["tasks"]["live_done"] == t["live_done"] and mcp["tasks"]["total"] == t["total"]
    assert mcp["phases"] == ph
    # the completion Progress: block: the same live pair, labelled
    code, done_out, err = run_cli(project, "complete", "R1", "--force")
    assert code == 0, err
    after = _json(project)["tasks"]
    assert f"tasks {after['live_done']}/{after['live']}" in done_out
    assert "live tasks" in done_out.split("Progress:")[1].split("\n")[0]
    # STATUS.md export
    code, _o, err = run_cli(project, "export", "status", "--out", "STATUS.md")
    assert code == 0, err
    md = (project / "STATUS.md").read_text()
    assert f"{after['live_done']} of {after['live']} live tasks done" in md
    assert f"= {after['total']}" in md


def test_phases_and_bugs_are_counted_the_same_way(project):
    run_cli(
        project, "bug", "found", "--summary", "it breaks", "--globs", "x.py", "--new", "--no-task"
    )
    d = _json(project)
    assert d["phases"]["total"] == 2
    assert d["phases"]["done"] + d["phases"]["open"] + d["phases"]["abandoned"] == 2
    b = d["bugs"]
    assert b["total"] == b["open"] + b["fixed"] + b["invalid"] and b["open"] >= 1
    _c, prose, _e = run_cli(project, "status")
    assert re.search(r"Phases: .*= 2", prose) and re.search(r"Bugs: .*= \d+", prose)


def test_fix_tasks_headline_knob(project, monkeypatch):
    base = _json(project)["tasks"]
    monkeypatch.setenv("DDFLOW_OUTPUT_STATUS_FIX_TASKS_IN_HEADLINE", "true")
    on = _json(project)["tasks"]
    assert on["live"] == base["live"] + base["fix"]
    assert on["live_done"] == base["live_done"] + base["fix_done"]
    assert on["total"] == base["total"]
    _c, prose, _e = run_cli(project, "status")
    assert f"{on['live_done']} of {on['live']} live tasks done" in prose
    assert "fix tasks included" in prose


def test_completed_list_shown_knob_and_bounded_label(project, monkeypatch):
    monkeypatch.setenv("DDFLOW_OUTPUT_STATUS_COMPLETED_SHOWN", "3")
    _c, prose, _e = run_cli(project, "status")
    block = prose.split("Completed")[1].split("\n\n")[0]
    assert "last 3 of 16" in block.split("\n")[0]
    assert block.count("[x]") == 3

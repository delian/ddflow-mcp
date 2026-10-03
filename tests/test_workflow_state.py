"""`ddflow workflow state` on a real log: it must run, and say what the queue holds."""

from __future__ import annotations

import json

from conftest import run_cli


def _task(repo, tid, *extra):
    code, out, err = run_cli(
        repo, "task", "add", tid, "--title", f"title {tid}", "--globs", f"{tid}.py", *extra
    )
    assert code == 0, out + err


def test_state_reports_queue_and_diagram_as_json(repo):
    _task(repo, "T1")
    _task(repo, "T2", "--needs", "T1")
    code, out, err = run_cli(repo, "--json", "workflow", "state")
    assert code == 0, out + err
    o = json.loads(out)
    assert [t["id"] for t in o["task_queue"]["ready"]] == ["T1"]
    assert o["task_queue"]["blocked"][0]["id"] == "T2"
    assert o["workflow_diagram"].startswith("flowchart LR")
    assert o["project"]["tasks"].get("open") == 2


def test_state_prose_lists_names_and_open_bugs(repo):
    _task(repo, "T1")
    run_cli(
        repo,
        "bug",
        "found",
        "--new",
        "--summary",
        "it breaks",
        "--severity",
        "high",
        "--title",
        "Breaks",
    )
    code, out, err = run_cli(repo, "workflow", "state")
    assert code == 0, out + err
    assert "T1: title T1" in out and "Breaks" in out and "mermaid" in out

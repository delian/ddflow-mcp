"""`ddflow report ITEM`: the fixed completion report, read back from the log (DX-agent-report).

Every fact a subagent used to retell in its last message -- shas, regression tests and whether
`complete` verified them, per-gate outcome with reviewer model and family, gates recorded by
hand, overrides, what was filed, what is open and what was not done -- is asserted here from
the log, so the report cannot drift from the record."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import pass_pipeline, run_cli
from helpers import git as _git

from ddflow.services import agent_brief as AB
from ddflow.services import report_render as RT

TEST_FILE = "def test_a():\n    pass\n"


def _land(repo, late: bool = False):
    """T1: worked in its worktree, every gate passed (the reviewers by a cross-family model),
    merged and completed, with a bug filed against it."""
    run_cli(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "Widen the widget", "--globs", "a.py")
    run_cli(repo, "bug", "found", "--id", "BX1", "--summary", "found on the way", "--item", "T1")
    code, out, err = run_cli(repo, "--json", "claim", "T1")
    assert code == 0, err
    tree = Path(json.loads(out)["worktree"])
    (tree / "a.py").write_text("a = 1\n")
    _git(tree, "add", "a.py")
    _git(tree, "commit", "-qm", "widen")
    pass_pipeline(repo, "T1", omit=("merge",))
    if late:  # work after the gates passed: they describe an earlier tree
        (tree / "b.py").write_text("b = 2\n")
        _git(tree, "add", "b.py")
        _git(tree, "commit", "-qm", "late")
    code, out, err = run_cli(repo, "merge", "T1", "--model", "claude-opus-5")
    assert code == 0, out + err
    code, out, err = run_cli(repo, "complete", "T1", "--model", "claude-opus-5")
    assert code == 0, out + err
    return repo


@pytest.fixture
def done(repo):
    return _land(repo)


def report(repo, *argv):
    code, out, err = run_cli(repo, "report", "T1", *argv)
    assert code == 0, out + err
    return out


def test_the_report_names_the_item_the_landing_and_every_gate(done):
    out = report(done)
    assert "# Report: T1 — Widen the widget" in out
    assert "## Landing" in out and "## Gates" in out and "## Not done" in out
    assert "merged " in out and "commits " in out
    for gate in ("research", "implement", "rubber_duck", "critic", "merge"):
        assert f"- {gate}: passed" in out, gate


def test_a_reviewer_gate_carries_its_model_and_family(done):
    out = report(done)
    line = next(x for x in out.splitlines() if x.startswith("- critic:"))
    assert "gemini-2.5-pro" in line and "family google" in line


def test_gates_recorded_by_hand_are_listed_with_their_evidence(done):
    out = report(done)
    hand = out.split("## Recorded by hand")[1].split("##")[0]
    assert "research: research evidence" in hand and "implement: implement evidence" in hand


def test_the_json_has_the_same_sections(done):
    data = json.loads(run_cli(done, "--json", "report", "T1")[1])
    assert data["item"]["id"] == "T1" and data["item"]["state"] == "done"
    assert {g["gate"] for g in data["gates"]} >= {"research", "critic", "merge"}
    assert data["landing"]["merge_sha"] and data["landing"]["commits"]
    assert [b["id"] for b in data["filed"]["bugs"]] == ["BX1"]
    assert "BX1 (bug, open)" in data["follow_ups"]
    assert data["not_done"] == []


def test_what_was_filed_and_what_is_still_open_are_reported(done):
    out = report(done)
    assert "BX1 (bug, open)" in out.split("## Filed during the item")[1].split("##")[0]
    assert "BX1 (bug, open)" in out.split("## Open follow-ups")[1]


def test_a_skipped_gate_and_its_reason_are_an_override(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "a.py")
    run_cli(repo, "gate", "skip", "T1", "dedupe", "--reason", "nothing to compare")
    out = run_cli(repo, "report", "T1")[1]
    assert "gate dedupe skipped: nothing to compare" in out.split("## Skips and overrides")[1]


def test_an_unfinished_item_lists_what_was_not_done(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "a.py")
    run_cli(repo, "gate", "record", "T1", "research", "--outcome", "failed", "--reason", "x")
    out = run_cli(repo, "report", "T1")[1]
    notdone = out.split("## Not done")[1]
    assert "gate research: failed" in notdone and "gate merge: never recorded" in notdone
    assert "not completed" in notdone and "not merged" in notdone


def test_a_regression_test_override_is_reported_with_its_reason(repo):
    run_cli(repo, "init")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_fix.py").write_text(TEST_FILE)
    run_cli(repo, "bug", "found", "--id", "B1", "--summary", "x")
    test = "tests/test_fix.py::test_a"
    code, out, err = run_cli(
        repo, "bug", "fixed", "B1", "--regression-test", test,
        "--skip-regression-verify", "--verify-reason", "no pre-fix source",
    )  # fmt: skip
    assert code == 0, out + err
    fix = json.loads(run_cli(repo, "--json", "show", "B1")[1]).get("fix_task") or "fix-B1"
    data = json.loads(run_cli(repo, "--json", "report", fix)[1])
    (row,) = data["regression"]["bugs"]
    assert row["bug"] == "B1" and row["tests"] == [test] and row["verified"] == "overridden"
    assert any(
        "--skip-regression-verify" in x and "no pre-fix source" in x for x in data["overrides"]
    )


def test_an_unknown_item_fails(repo):
    run_cli(repo, "init")
    assert run_cli(repo, "report", "NOPE")[0] == 1


def test_a_project_template_replaces_the_layout(done):
    d = done / ".ddflow" / "prompts"
    d.mkdir(parents=True, exist_ok=True)
    (d / "agent_report.md").write_text("<!-- section: header -->\nDONE {{ item }}\n{{ gates }}\n")
    out = report(done)
    assert out.startswith("DONE T1") and "## Landing" not in out


def test_the_shipped_template_has_every_section_the_text_provides():
    text = (Path(RT.__file__).parents[1] / "templates" / "prompts" / "agent_report.md").read_text()
    names = [n for n, _ in AB.split(text)]
    assert names == [
        "header", "landing", "tests", "gates", "by_hand",
        "overrides", "filed", "follow_ups", "not_done",
    ]  # fmt: skip


def test_the_launch_brief_ends_the_run_with_this_report(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "a.py")
    out = run_cli(repo, "brief", "--for-agent", "sub-1", "T1")[1]
    assert "ddflow report T1 --agent sub-1" in out


def test_a_gate_that_ran_before_the_last_commit_is_marked_as_an_earlier_tree(repo):
    out = report(_land(repo, late=True))
    assert "ran on an earlier tree" in out.split("## Gates")[1].split("##")[0]


def test_a_gate_that_ran_on_the_landed_tree_is_not(done):
    assert "earlier tree" not in report(done)


def test_the_report_is_reachable_over_mcp_as_show_with_report(done):
    from ddflow.surfaces.mcp import Server

    reply = Server(done).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_show", "arguments": {"id": "T1", "report": True}},
        }
    )
    body = json.dumps(reply["result"])
    assert "Recorded by hand" in body and "rubber_duck" in body


def test_landing_without_listed_commits_prints_no_dangling_count():
    d = {
        "item": {"id": "I", "title": "t", "agent": "a", "state": "done"},
        "landing": {
            "merge_sha": "0123456789abcdef", "base": "main", "branch": "b", "branch_head": "",
            "commits": [], "commits_total": 3, "completed_at": "", "completed_by": "",
        },
        "regression": {"bugs": [], "test_files": []},
        "gates": [], "overrides": [], "filed": {"bugs": [], "tasks": []},
        "follow_ups": [], "not_done": [],
    }  # fmt: skip
    assert "(+3 more)" not in RT.variables(d)["landing"]

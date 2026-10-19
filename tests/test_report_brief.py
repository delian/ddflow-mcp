"""`ddflow report ITEM --brief`: the 8-line form, and the brief line telling agents to use it
(DX-agent-final-summary)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from test_agent_report import _land

from ddflow.services import report_brief as RB


def test_the_brief_is_eight_lines_with_the_landing_sha_and_the_filed_ids(repo):
    _land(repo)
    code, out, err = run_cli(repo, "report", "T1", "--brief")
    assert code == 0, err
    got = out.rstrip("\n").split("\n")
    assert len(got) == RB.LINES == 8
    assert got[0].startswith("T1: done")
    sha = json.loads(run_cli(repo, "--json", "report", "T1")[1])["landing"]["merge_sha"][:12]
    assert f"landed: {sha} on" in got[1]
    assert "BX1" in got[5] and got[7] == "not done: none"


def test_a_line_is_cut_to_the_knob(repo):
    _land(repo)
    assert run_cli(repo, "config", "--set", "session.report_brief_chars", "20")[0] == 0
    out = run_cli(repo, "report", "T1", "--brief")[1].rstrip("\n").split("\n")
    # free-text lines are cut; the landing and id lines are whole
    cut = [line for i, line in enumerate(out) if i not in RB._WHOLE]
    assert all(len(line) <= 20 for line in cut) and any(line.endswith("…") for line in cut)


def test_the_full_report_is_unchanged_and_the_agent_brief_points_at_brief(repo):
    _land(repo)
    full = run_cli(repo, "report", "T1")[1]
    assert "## Gates" in full
    run_cli(repo, "task", "add", "T2", "--title", "x", "--globs", "b.py")
    text = run_cli(repo, "brief", "--for-agent", "w1", "T2")[1]
    assert "ddflow report T2 --agent w1 --brief" in text


def test_an_unmerged_item_reports_not_merged_and_ids_are_never_cut():
    d = {
        "item": {"id": "T9", "state": "open", "agent": "a"},
        "landing": {"merge_sha": None, "base": ""},
        "regression": {"bugs": [], "test_files": []},
        "gates": [],
        "overrides": [],
        "follow_ups": [],
        "not_done": ["x" * 400],
        "filed": {
            "bugs": [{"id": f"BUG-{n:04d}-long-id", "open": True} for n in range(30)],
            "tasks": [],
        },
    }
    out = RB.render(d, 20).split("\n")
    assert out[1] == "landed: not merged"
    assert "BUG-0029-long-id" in out[5] and not out[5].endswith("…")
    assert out[7].endswith("…") and len(out[7]) == 20


def test_the_brief_is_reachable_over_mcp_as_show_report_brief(repo):
    from ddflow.api import show_report

    _land(repo)
    out = show_report(repo, "T1", brief=True)
    assert out.data["item"]["text"].count("\n") == 8


def test_the_regression_line_with_bug_ids_and_tests_is_not_cut():
    bugs = [
        {
            "bug": f"B{n}",
            "tests": [f"tests/test_x.py::test_{n}_" + "a" * 40],
            "verified": "verified",
        }
        for n in range(6)
    ]
    d = {
        "item": {"id": "T", "state": "done", "agent": "a"},
        "landing": {"merge_sha": "a" * 40, "base": "main"},
        "regression": {"bugs": bugs, "test_files": []},
        "gates": [],
        "overrides": [],
        "follow_ups": [],
        "not_done": [],
        "filed": {"bugs": [], "tasks": []},
    }
    line = RB.render(d, 20).split("\n")[2]
    assert "B5: " in line and not line.endswith("…")

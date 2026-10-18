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
    assert all(len(line) <= 20 for line in out) and any(line.endswith("…") for line in out)


def test_the_full_report_is_unchanged_and_the_agent_brief_points_at_brief(repo):
    _land(repo)
    full = run_cli(repo, "report", "T1")[1]
    assert "## Gates" in full
    run_cli(repo, "task", "add", "T2", "--title", "x", "--globs", "b.py")
    text = run_cli(repo, "brief", "--for-agent", "w1", "T2")[1]
    assert "ddflow report T2 --agent w1 --brief" in text

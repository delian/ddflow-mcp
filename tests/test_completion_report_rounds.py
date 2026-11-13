"""The completion report's per-review-gate line (DX-agent-final-report-rounds): rounds used
of the cap, every forced round's reason, `passed on refutation`, and the note when a reviewer
did not see the final commit -- surfaced in both the full report and the 8-line brief."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_gate_econ_review as GE
from conftest import run_cli

import ddflow.api.review as api
from ddflow.services import report_brief as RB
from ddflow.services import report_render as RT


def test_the_gate_line_names_rounds_used_of_the_cap_when_a_review_ran(repo, tmp_path):
    GE._setup(repo, tmp_path, review_toml="[review]\nmax_rounds = 4\n")
    api.review(repo, gate=GE.GATES, item="T1", branch="feat")
    code, out, err = run_cli(repo, "report", "T1")
    assert code == 0, err
    # one full round of four: the finding the fake reviewer flags keeps the gate failed,
    # but the round itself is what the line reports.
    assert "1/4 rounds" in out, out


def test_the_gate_line_carries_refutation_forced_and_a_missed_final_commit():
    row = {
        "gate": "rubber_duck",
        "outcome": "passed",
        "by": "ph-coordination",
        "reason": "",
        "model": "",
        "family": "xai",
        "reviewer": "grok-4.7",
        "rounds": 0,
        "review_rounds": {
            "used": 3,
            "full": 2,
            "delta": 1,
            "max": 4,
            "forced": ["not finding any more"],
            "refutation": {"refuted": 2, "confirmed": 1},
            "reviewed_head": "abc1111",
            "missed_final": "def2222",
        },
    }
    line = RT._gate_line({**row, "triage": None})
    assert "3/4 rounds (2 full, 1 delta)" in line, line
    assert "forced: not finding any more" in line, line
    assert "passed on refutation (2 refuted, 1 confirmed)" in line, line
    assert "reviewer did not see the final commit (reviewed abc1111, final def2222)" in line, line


def test_the_brief_review_line_is_rounds_of_the_cap_and_refutation():
    line = RB._review_line(
        {
            "gate": "critic",
            "review_rounds": {
                "used": 2,
                "max": 3,
                "refutation": {"refuted": 1, "confirmed": 0},
            },
        }
    )
    assert line == "critic 2/3 (on refutation)", line

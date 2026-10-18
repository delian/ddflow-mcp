"""The report reuses the gate triage wording and one title cut (DX-report-share-helpers)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.services import report as R
from ddflow.services import report_render as RT


def _gate(**kw):
    g = {
        "gate": "critic",
        "outcome": "passed",
        "model": "",
        "by": "",
        "reviewer": "",
        "family": "",
        "rounds": 0,
        "reason": "",
    }
    return g | kw


def test_the_gate_line_uses_the_gate_triage_wording_including_the_settled_clause():
    t = {"findings": 3, "refuted": 1, "confirmed": 1, "untriaged": 0, "settled_by_pass": 1}
    line = RT._gate_line(_gate(triage=t))
    assert "3 finding(s): 1 refuted, 1 confirmed, 0 untriaged, 1 settled by the later pass" in line


def test_one_title_cut_for_every_kind_of_record():
    assert R._title("", "second") == "second" and R._title(None) == ""
    assert len(R._title("x" * 500)) == R.TITLE_CAP
    assert len(R._title("x" * 500, cap=R.BUG_SUMMARY_CAP)) == 120 > R.TITLE_CAP

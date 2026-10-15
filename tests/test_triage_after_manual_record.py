"""Bug B754cfb0c96: a hand-recorded gate must not orphan its untriaged review findings.

`ddflow gate record <id> critic --outcome passed` replaced the gate's evidence, so the
numbered findings of the earlier `ddflow review` vanished and `review triage` could not
find them. They now stay on the record with their numbers, and the record warns.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import run_cli
from test_review_triage import _full_review, _setup, _state, _triage

from ddflow.services import gates as G


def _record_by_hand(repo: Path):
    return run_cli(
        repo, "gate", "record", "T1", "critic", "--outcome", "passed",
        "--evidence", "read both findings; looked at the code by hand",
    )  # fmt: skip


def test_triage_still_finds_the_findings_after_a_manual_gate_record(repo, tmp_path):
    _setup(repo, tmp_path)
    _full_review(repo)
    code, out, err = _record_by_hand(repo)
    assert code == 0, out + err
    assert "untriaged" in out + err and "#1" in out + err and "#2" in out + err
    res = _triage(repo, 2)
    assert res.exit == 0, res.reason
    counts = G.triage_counts(_state(repo), "critic")
    assert counts == {"findings": 2, "refuted": 1, "confirmed": 0, "untriaged": 1}
    _code, status, _err = run_cli(repo, "gate", "status", "T1")
    assert "2 finding(s): 1 refuted, 0 confirmed, 1 untriaged" in status, status

"""Re-triaging a refuted finding to confirmed revokes the gate's pass on refutation (B31e91454f8).

A gate whose review budget is spent passes ON REFUTATION once every finding is refuted with
its probe. Re-triaging one of those findings to CONFIRMED afterwards must not leave the gate
showing `[x] PASSED ON REFUTATION` while a finding that failed the round is now confirmed and
unfixed: the pass is revoked and the stale flag is dropped.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_review_triage import _full_review, _setup, _state

import ddflow.api.review_settle as ST
from ddflow.services.gates.outcomes import on_refutation


def _tri(repo, n, verdict="refuted", probe="ran it: no such bug"):
    return ST.triage(repo, "T1", gate="critic", finding=n, verdict=verdict, probe=probe)


def test_retriage_to_confirmed_revokes_the_pass_on_refutation(repo, tmp_path):
    _setup(repo, tmp_path)
    _full_review(repo)
    _tri(repo, 1)
    last = _tri(repo, 2)
    assert last.exit == 0
    assert last.data["passed_on_refutation"] is True
    gate = _state(repo).gates["critic"]
    assert gate.outcome == "passed"
    assert gate.evidence["passed_on_refutation"]["confirmed"] == 0

    # finding 1 was refuted; re-triaging it CONFIRMED revives a confirmed, unfixed defect.
    redone = _tri(repo, 1, "confirmed", "reproduced: a.py:1 is really wrong on this input")
    assert redone.exit == 0, redone.reason
    gate = _state(repo).gates["critic"]
    assert "passed_on_refutation" not in (gate.evidence or {}), "the stale refutation flag must be dropped"
    assert gate.outcome != "passed", "a confirmed unfixed finding must unpass the gate"
    assert on_refutation(_state(repo), "critic") is None, "the display path must not show a refutation pass"

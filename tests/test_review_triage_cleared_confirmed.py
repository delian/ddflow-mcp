"""bug B-review-gate-stuck-failed-after-triage: a confirmed finding a later delta round did
not raise again must not keep blocking the pass-on-refutation.

A gate whose every finding is triaged, with the confirmed ONES cleared by a later delta
that did not raise them again, used to stay `failed` forever: `review triage` counted the
confirmed findings from the merged record alone, so it never recorded the passed-on-
refutation, and a plain re-review answered "nothing new to review". The fix makes the
pass-on-refutation run when every confirmed finding is cleared after a delta (the same
rule `merge`/`complete` already use), so the last round's refutation settles the gate.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_complete_failed_review_gate import _commit, _failed_and_confirmed, _setup

import ddflow.api.review as api
import ddflow.api.review_settle as ST
from ddflow.core.model import fold
from ddflow.infra.log import EventLog


def _state(repo: Path):
    return fold(EventLog(repo).read_all(), strict=False).items["T1"]


def test_a_cleared_confirmed_finding_does_not_block_the_refutation_pass(repo, tmp_path):
    _setup(repo, tmp_path)
    _failed_and_confirmed(repo)  # R1 full: #1 confirmed
    # Fix #1, and draw a NEW HIGH finding (#2) so the delta re-reviews #1 WITHOUT raising it:
    # #1 is now cleared after a delta, #2 is the last round's only finding.
    _commit(repo, "x.py", "x = 1\n")
    _commit(repo, "z.py", "z = 1  # FINDME-H\n")
    out = api.review(repo, gate="critic", item="T1", branch="feat")  # R2 delta -> #2 only
    assert out.data["outcome"] == "failed", out.data
    ev = fold(EventLog(repo).read_all(), strict=False).items["T1"].gates["critic"].evidence
    assert len(ev["delta_raised"]) == 1 and ev["findings"] == 2, ev

    t = ST.triage(repo, "T1", gate="critic", finding=2, verdict="refuted", probe="z.py is fine")

    assert t.exit == 0, t.reason
    assert t.data["passed_on_refutation"] is True, t.data["text"]
    assert "passed ON REFUTATION" in t.data["text"], t.data["text"]
    assert _state(repo).gates["critic"].outcome == "passed"

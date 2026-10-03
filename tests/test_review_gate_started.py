"""A review appends gate.started before it calls the reviewer (bug B7ed5137d45).

Without it `flowsignals.reviewer_latency_ratio` has no started-to-outcome pairs on a real
log, so the adaptive flow controller never sees reviewer latency.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ddflow.api.review as api
from ddflow.infra.log import EventLog
from test_review_budget import _setup


def _gate_events(repo, gate):
    return [
        e.kind
        for e in EventLog(repo).read_all()
        if e.subject == "T1" and e.kind.startswith("gate.") and e.data.get("gate") == gate
    ]


def test_review_logs_gate_started_before_its_outcome(repo, tmp_path):
    _setup(repo, tmp_path)
    api.review(repo, gate="critic", item="T1")
    kinds = _gate_events(repo, "critic")
    assert "gate.started" in kinds, kinds
    assert kinds.index("gate.started") < max(i for i, k in enumerate(kinds) if k != "gate.started")


def test_a_refused_review_does_not_log_started(repo, tmp_path):
    _setup(repo, tmp_path)
    api.review(repo, gate="critic", item="T1")
    api.review(repo, gate="critic", item="T1", full=True)
    before = _gate_events(repo, "critic").count("gate.started")
    out = api.review(repo, gate="critic", item="T1", full=True)  # third full round: refused
    assert out.exit == 3
    assert _gate_events(repo, "critic").count("gate.started") == before

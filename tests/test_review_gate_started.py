"""A review appends gate.started before it calls the reviewer (bug B7ed5137d45).

It marks when a review began, for readers of the log (`ddflow history`, `rates`).
Reviewer latency itself no longer pairs it with the outcome: it reads the `elapsed_s` the
review records, so a killed run or a triage gap cannot pose as a slow reviewer (bug
B1c5dbe3103).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_review_budget import _setup

import ddflow.api.review as api
from ddflow.infra.log import EventLog


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


def test_started_does_not_move_a_recorded_gates_timestamp(repo, tmp_path):
    from ddflow.core.model import fold

    _setup(repo, tmp_path)
    api.review(repo, gate="critic", item="T1")
    rec = fold(EventLog(repo).read_all(), strict=False).items["T1"].gates["critic"]
    EventLog(repo).append("gate.started", "T1", {"gate": "critic"})
    after = fold(EventLog(repo).read_all(), strict=False).items["T1"].gates["critic"]
    assert after.at == rec.at and after.outcome == rec.outcome

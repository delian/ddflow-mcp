"""B29667ba4b4: a gate's status is its LATEST outcome, and the untriaged count belongs
to the round that outcome belongs to -- a later pass settles the findings it follows."""

from __future__ import annotations

import pytest
from conftest import run_cli
from test_gate_econ_review_refutation import _gate, _setup, _status_line

import ddflow.api.review as api


@pytest.fixture(autouse=True)
def _full_rounds(monkeypatch):
    monkeypatch.setenv("DDFLOW_REVIEW_DELTA_DEFAULT", "0")


def _hand(repo, outcome, *extra):
    code, _out, err = run_cli(repo, "gate", "record", "T1", "critic", "--outcome", outcome, *extra)
    assert code == 0, err


def _mark(repo) -> str:
    _c, out, _e = run_cli(repo, "gate", "status", "T1")
    return next(line for line in out.splitlines() if " critic" in line)


def test_failed_then_passed_with_evidence_shows_passed(repo, tmp_path):
    _setup(repo, tmp_path, 3)
    assert api.review(repo, gate="critic", item="T1").data["outcome"] == "failed"
    assert "[!] critic" in _mark(repo)
    _hand(repo, "passed", "--evidence", "all 3 findings confirmed and fixed")
    assert _gate(repo).outcome == "passed"
    assert "[x] critic" in _mark(repo)


def test_findings_settled_by_a_later_pass_are_not_untriaged(repo, tmp_path):
    _setup(repo, tmp_path, 3)
    api.review(repo, gate="critic", item="T1")
    _hand(repo, "passed", "--evidence", "all 3 findings confirmed and fixed")
    line = _status_line(repo)
    assert "0 untriaged" in line
    assert "3 settled by the later pass" in line


def test_a_still_failed_round_keeps_its_untriaged_count(repo, tmp_path):
    _setup(repo, tmp_path, 3)
    api.review(repo, gate="critic", item="T1")
    assert "3 untriaged" in _status_line(repo)
    _hand(repo, "unavailable", "--reason", "reviewer down")
    assert "3 untriaged" in _status_line(repo)
    api.triage(repo, "T1", gate="critic", finding=1, verdict="confirmed", probe="saw it")
    assert "2 untriaged" in _status_line(repo)


def test_a_hand_pass_with_its_own_findings_does_not_settle_them(repo, tmp_path):
    """Only findings CARRIED from the earlier round are settled by the pass."""
    from ddflow.core.handlers.gates import _h_gate
    from ddflow.core.model import State

    st = State()
    ev = type("E", (), {"ts": "t", "agent": "a", "subject": "T1", "data": {}})
    first = ev()
    first.data = {"gate": "critic", "evidence": {"chunk_findings": [{"digest": "a"}]}}
    _h_gate("failed")(st, first)
    second = ev()
    second.data = {"gate": "critic", "evidence": {"chunk_findings": [{"digest": "b"}]}}
    _h_gate("passed")(st, second)
    rec = st.items["T1"].gates["critic"]
    assert rec.evidence["chunk_findings"] == [{"digest": "b"}]
    assert "findings_carried" not in rec.evidence


def test_triage_without_a_verdict_points_at_the_findings(repo, tmp_path):
    _setup(repo, tmp_path, 2)
    api.review(repo, gate="critic", item="T1")
    out = api.triage(repo, "T1", gate="critic", finding=1, verdict="", probe="")
    assert out.exit != 0
    assert "ddflow review show T1" in out.reason

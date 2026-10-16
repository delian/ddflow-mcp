"""Bug Bf065905087: a hand `gate record` of a review gate keeps the reviewer."""

from ddflow.config import Config
from ddflow.core.handlers.gates import _h_gate
from ddflow.core.model import State
from ddflow.services.gates import reviewers as RV

_E = type("E", (), {"ts": "t", "agent": "author", "subject": "T1", "data": {}})
_ROUND = {
    "model": "deepseek-v3",
    "family": "deepseek",
    "reviewer": "lan-deepseek",
    "reviewed_sha": "abc1234",
    "review_kind": "full",
}


def _fold(*recs):
    st = State()
    for outcome, evidence in recs:
        e = _E()
        e.data = {"gate": "critic", "evidence": dict(evidence)}
        _h_gate(outcome)(st, e)
    return st, st.items["T1"].gates["critic"]


def test_hand_pass_keeps_reviewer_model_and_family():
    _st, rec = _fold(("failed", _ROUND), ("passed", {"note": "triaged"}))
    assert rec.evidence["model"] == "deepseek-v3"
    assert rec.evidence["family"] == "deepseek"
    assert rec.evidence["reviewer"] == "lan-deepseek"
    assert rec.evidence["reviewed_sha"] == "abc1234"
    assert "model" in rec.evidence["reviewer_carried"]


def test_explicit_model_wins_and_drops_the_stale_family():
    _st, rec = _fold(("failed", _ROUND), ("passed", {"model": "gpt-5"}))
    assert rec.evidence["model"] == "gpt-5"
    assert "family" not in rec.evidence and "reviewer" not in rec.evidence
    assert rec.evidence["reviewed_sha"] == "abc1234"


def test_explicit_sha_wins():
    _st, rec = _fold(("failed", _ROUND), ("passed", {"reviewed_sha": "def5678"}))
    assert rec.evidence["reviewed_sha"] == "def5678"


def test_no_prior_review_records_what_the_caller_gave():
    _st, rec = _fold(("passed", {"note": "n", "model": "m1"}))
    assert rec.evidence == {"note": "n", "model": "m1"}
    _st, rec = _fold(("passed", {"note": "n"}))
    assert "reviewer_carried" not in rec.evidence and "model" not in rec.evidence


def test_independence_is_satisfied_after_a_hand_pass():
    st, _rec = _fold(("failed", _ROUND), ("passed", {"note": "triaged"}))
    ok, why = RV.reviewer_independence(st, Config(), "T1", "claude-opus-5")
    assert ok, why

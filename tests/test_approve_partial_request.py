"""B87cd549ab0: an agent files the request for a person-only baseline-red partial by hand
(`approvals request approve.partial`), and no policy rule or standing window grants it."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import approvals as API
from ddflow.core.handlers.asks import asks_of
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import approval as AP
from ddflow.services import gate_accept as GA
from ddflow.services.approvals import policy as PO
from ddflow.services.approvals.canon import Canon


@pytest.fixture(autouse=True)
def _a_person(monkeypatch):
    for var in ("DDFLOW_AGENT", *AP.HARNESS_MARKERS):
        monkeypatch.delenv(var, raising=False)


def _state(repo):
    return fold(EventLog(repo, "bot").read_all(), strict=False)


def _partial(repo, *, red=True, auto=False):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "T1", "--globs", "t.py")
    base = {"red": red, "base": "main", "ids": ["tests/t.py::test_x"]}
    if red:
        base["auto"] = {"accepted": auto, "why": "item reaches tests/t.py"}
    EventLog(repo, "ddflow-gate-run").append(
        "gate.partial",
        "T1",
        {"gate": "unit_tests", "by": "ddflow", "reason": "baseline-red: tests/t.py::test_x",
         "evidence": {"baseline": base}},
    )  # fmt: skip


def test_an_agent_requests_a_person_only_partial_by_hand(repo):
    _partial(repo)
    out = API.approvals_request(
        repo, "approve.partial", item="T1", target="unit_tests", reason="base is red", agent="bot"
    )
    assert out.exit == 0, out
    row = asks_of(_state(repo))[out.data["id"]]
    it = _state(repo).items["T1"]
    canon = Canon(verb="approve.partial", agent=row["requested_by"], item="T1",
                  target="unit_tests", state=GA.digest(it, "unit_tests"))  # fmt: skip
    assert row["digest"] == canon.digest() == out.data["digest"]
    assert row["uses"] == 1


def test_a_failed_or_unmeasured_gate_cannot_be_requested(repo):
    _partial(repo, red=False)
    out = API.approvals_request(
        repo, "approve.partial", item="T1", target="unit_tests", agent="bot"
    )
    assert out.exit != 0 and "baseline-red" in str(out)
    run_cli(repo, "gate", "record", "T1", "critic", "--outcome", "failed", "--reason", "f")
    out = API.approvals_request(repo, "approve.partial", item="T1", target="critic", agent="bot")
    assert out.exit != 0
    out = API.approvals_request(repo, "approve.partial", item="NOPE", target="x", agent="bot")
    assert out.exit != 0


def test_no_rule_or_window_grants_the_requested_partial(repo):
    _partial(repo)
    cfg_path = repo / ".ddflow" / "config.toml"
    cfg_path.write_text(
        cfg_path.read_text()
        + "\n[approvals]\nstanding_window_s = 86400\nauto_approve_after_retries = 1\n"
        'policy = ["accept_baseline_red", "complete_unavailable", "skip_after_unavailable"]\n'
    )
    for _ in range(3):
        out = API.approvals_request(
            repo, "approve.partial", item="T1", target="unit_tests", agent="bot"
        )
        assert out.exit == 0, out
    from ddflow.api._base import _load_fresh

    log, cfg, st = _load_fresh(repo, "bot")
    assert (
        cfg.approvals.standing_window_s == 86400 and "accept_baseline_red" in cfg.approvals.policy
    )
    it = st.items["T1"]
    canon = Canon(verb="approve.partial", agent="bot", item="T1", target="unit_tests",
                  state=GA.digest(it, "unit_tests"))  # fmt: skip
    assert PO.apply(log, cfg, st, canon) is None
    assert not [r for r in asks_of(_state(repo)).values() if r["answered_by"]]


def _window_offered(repo, ask_id: str) -> bool:
    """Does the `approvals ask` payload offer the standing-window choice for this ask?"""
    from ddflow.api import approvals_harness as AH

    res = AH.approvals_ask(repo, ask_id, agent="bot")
    body = res.data["input"]
    return any("standing window" in o["label"] for o in body["questions"][0]["options"])


def test_the_standing_window_option_is_hidden_for_a_person_only_partial(repo):
    """Bdeb98fa2d1: 'approve + standing window (24h)' is not offered when the target is a
    measured baseline-red partial only a person accepts -- the window never covers it."""
    _partial(repo)  # red=True, auto=False: person-only
    out = API.approvals_request(
        repo, "approve.partial", item="T1", target="unit_tests", reason="base is red", agent="bot"
    )
    assert out.exit == 0, out
    assert not _window_offered(repo, out.data["id"])


def test_the_standing_window_option_is_kept_for_an_auto_accepted_partial(repo):
    """The option is only hidden for the PERSON-ONLY partial: an auto-accepted baseline-red
    partial (not `GA.person_only`) stays a generic T2 request the window may pre-authorise."""
    _partial(repo, auto=True)
    out = API.approvals_request(
        repo, "approve.partial", item="T1", target="unit_tests", reason="base is red", agent="bot"
    )
    assert out.exit == 0, out
    assert _window_offered(repo, out.data["id"])

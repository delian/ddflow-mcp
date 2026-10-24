"""RA-policy: standing approval rules ship OFF, approve only their own verb within a cap, and are
recorded as policy -- never as a person (D-RA-4, D-RA-Q8)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.config import Config
from ddflow.core.handlers.asks import asks_of
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import actors as A
from ddflow.services import approval as AP
from ddflow.services.approvals import audit as AU
from ddflow.services.approvals import consume as C
from ddflow.services.approvals import gate_skip as GS
from ddflow.services.approvals import policy as PO
from ddflow.services.ask import engine as E


@pytest.fixture(autouse=True)
def _a_person(monkeypatch):
    for var in ("DDFLOW_AGENT", *AP.HARNESS_MARKERS):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def proj(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "t.py")
    for _ in range(3):
        assert run_cli(repo, "gate", "record", "T1", "critic", "--outcome", "unavailable",
                       "--reason", "reviewer down")[0] == 0  # fmt: skip
    assert (
        run_cli(repo, "gate", "record", "T1", "critic", "--outcome", "failed", "--reason", "f")[0]
        == 0
    )
    return repo


def _state(repo):
    return fold(EventLog(repo, "bot").read_all(), strict=False)


def _canon(repo, log):
    it = _state(repo).items["T1"]
    why = "failed with no triaged findings on record"
    return GS.canon_for(it, "critic", "T1", why, log.agent_id, "skipped")


def _cfg(**over):
    cfg = Config()
    for k, v in over.items():
        setattr(cfg.approvals, k, v)
    return cfg


def test_the_rules_ship_off_and_ask_a_person(proj):
    log = EventLog(proj, "bot")
    got = C.operator_gate(log, Config(), _canon(proj, log), reason="r")
    assert not got.ok and got.ask_id and "requested" in got.message
    assert Config().approvals.policy == []


def test_an_enabled_rule_approves_its_verb_and_is_recorded_as_policy(proj):
    log = EventLog(proj, "bot")
    cfg = _cfg(policy=["skip_after_unavailable"])
    got = C.operator_gate(log, cfg, _canon(proj, log), reason="r")
    assert (
        got.ok and got.stamp["via"] == "policy" and got.stamp["policy"] == "skip_after_unavailable"
    )
    row = asks_of(_state(proj))[got.ask_id]
    assert row["policy"] is True and row["answered_by"] == "policy:skip_after_unavailable"
    assert row["human"] is False and (row["used"], row["uses"]) == (1, 1)
    assert any(
        "granted by standing rule policy:skip_after_unavailable" in f
        for f in AU.findings(_state(proj), cfg)
    )
    rows = AU.rows(_state(proj), cfg)
    assert rows[0]["approver"] == "policy:skip_after_unavailable" and rows[0]["channel"] == "policy"


def test_the_cap_and_the_condition_bound_a_rule(proj):
    log = EventLog(proj, "bot")
    cfg = _cfg(policy=["skip_after_unavailable"])
    assert C.operator_gate(log, cfg, _canon(proj, log)).ok
    again = C.operator_gate(log, cfg, _canon(proj, log))
    assert not again.ok  # one use per item and target: a person is asked
    cfg.approvals.policy_max_uses = 2
    assert C.operator_gate(log, cfg, _canon(proj, log)).ok
    cfg.approvals.policy_unavailable_runs = 4  # only three unavailable runs on record
    cfg.approvals.policy_max_uses = 9
    assert not C.operator_gate(log, cfg, _canon(proj, log)).ok


def test_a_rule_approves_nothing_but_its_own_verb_and_unknown_ids_do_nothing(proj):
    log = EventLog(proj, "bot")
    cfg = _cfg(policy=["accept_baseline_red", "no_such_rule"])
    assert not C.operator_gate(log, cfg, _canon(proj, log)).ok  # a skip is not an accept
    assert set(PO.RULES) == {"skip_after_unavailable", "accept_baseline_red"}
    assert all(r.verb in {"gate.skip.failed_review", "approve.partial"} for r in PO.RULES.values())


def test_policy_is_not_a_way_to_answer_as_a_person(proj):
    log = EventLog(proj, "bot")
    cfg = _cfg(policy=["skip_after_unavailable"])
    ask = E.request(log, cfg, subject="x", digest="d")
    policy_actor = A.resolve_actor(cfg, "policy:skip_after_unavailable", requested_agent="bot")
    with pytest.raises(E.AskRefused):
        E.answer(log, cfg, ask.id, "approved", actor=policy_actor, digest="d")
    assert "policy" not in E.ANSWER_SOURCES


def test_a_policy_use_reaches_the_gate_skip_end_to_end(proj):
    cfgfile = proj / ".ddflow" / "config.toml"
    cfgfile.write_text(cfgfile.read_text() + '\n[approvals]\npolicy = ["skip_after_unavailable"]\n')
    code, out, err = run_cli(proj, "gate", "skip", "T1", "critic", "--reason", "r", agent="bot")
    assert code == 0, (out, err)
    run = [r for r in _state(proj).items["T1"].runs_of("critic") if r.outcome == "skipped"][-1]
    assert run.evidence["approval"]["via"] == "policy"
    assert run.evidence["approval"]["approved_by"] == "policy:skip_after_unavailable"


def test_a_rule_never_approves_a_verb_that_needs_more_than_level_one(proj, monkeypatch):
    log = EventLog(proj, "bot")
    cfg = _cfg(policy=["skip_after_unavailable"])
    t3 = PO.Rule("t3rule", "config.reviewer_command", "x", lambda it, target, c: True)
    monkeypatch.setitem(PO.RULES, "t3rule", t3)
    cfg.approvals.policy = ["t3rule"]
    from ddflow.services.approvals.canon import Canon

    canon = Canon(verb="config.reviewer_command", agent="bot", item="T1")
    assert not C.operator_gate(log, cfg, canon).ok


def test_a_standing_rule_is_consulted_before_the_request_hint_of_a_no_auto_site(proj, monkeypatch):
    log = EventLog(proj, "bot")
    rule = PO.Rule("always_complete", "complete.force", "x", lambda it, target, c: True)
    monkeypatch.setitem(PO.RULES, "always_complete", rule)
    cfg = _cfg(policy=["always_complete"])
    from ddflow.services.approvals.canon import Canon

    canon = Canon(verb="complete.force", agent="bot", item="T1", state="s")
    assert C.operator_gate(log, cfg, canon, auto=False).ok  # not "re-run with --approval request"
    assert not C.operator_gate(log, Config(), canon, auto=False).ok

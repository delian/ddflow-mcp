"""RA-policy: standing approval rules ship OFF, approve only their own verb within a cap, and are
recorded as policy -- never as a person (D-RA-4, D-RA-Q8)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.config import Config
from ddflow.core import clock
from ddflow.core.handlers.asks import asks_of
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import actors as A
from ddflow.services import approval as AP
from ddflow.services.approvals import audit as AU
from ddflow.services.approvals import consume as C
from ddflow.services.approvals import gate_skip as GS
from ddflow.services.approvals import policy as PO
from ddflow.services.approvals.canon import Canon
from ddflow.services.ask import engine as E


@pytest.fixture(autouse=True)
def _a_person(monkeypatch):
    for var in ("DDFLOW_AGENT", *AP.HARNESS_MARKERS):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def proj(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "t.py")
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
    assert set(PO.RULES) == {
        "skip_after_unavailable",
        "accept_baseline_red",
        "complete_unavailable",
        "claim_unavailable",
    }
    assert all(
        r.verb in {"gate.skip.failed_review", "approve.partial", "complete.force", "claim.force"}
        for r in PO.RULES.values()
    )


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


def _frozen_clock(monkeypatch):
    from datetime import UTC, datetime

    t = [datetime(2026, 1, 1, tzinfo=UTC)]
    monkeypatch.setattr(clock, "now_utc", lambda: t[0])
    return t


def test_a_windowed_rule_answers_complete_force_with_its_digest_while_the_window_is_open(
    proj,
    monkeypatch,
):
    _frozen_clock(monkeypatch)
    log = EventLog(proj, "bot")
    canon = Canon(verb="complete.force", agent="bot", item="T1", state="s")
    cfg = _cfg(policy=["complete_unavailable"], policy_max_uses=99)
    got = C.operator_gate(log, cfg, canon)
    assert got.ok and got.stamp["via"] == "policy" and got.stamp["policy"] == "complete_unavailable"
    row = asks_of(_state(proj))[got.ask_id]
    assert row["policy"] is True and row["answered_by"] == "policy:complete_unavailable"
    assert row["digest"] == canon.digest()
    assert PO.window_start(_state(proj), cfg) > 0.0  # the fold persisted the opened window


def test_a_windowed_rule_stops_the_instant_the_window_lapses_and_a_person_is_asked(
    proj,
    monkeypatch,
):
    from datetime import timedelta

    frozen = _frozen_clock(monkeypatch)
    log = EventLog(proj, "bot")
    canon = Canon(verb="complete.force", agent="bot", item="T1", state="s")
    claim = Canon(verb="claim.force", agent="bot", item="T1", state="s")
    cfg = _cfg(
        policy=["complete_unavailable", "claim_unavailable"],
        policy_max_uses=99,
        standing_window_s=120,  # window below use_ttl_s (3600): lapse must be the WINDOW, not the TTL
    )
    first = C.operator_gate(log, cfg, canon)
    assert first.ok  # opens the window, anchored now
    # A SECOND generic T2 refusal while the window is still open is also auto-approved ...
    still = C.operator_gate(log, cfg, claim)
    assert (
        still.ok and still.stamp["via"] == "policy" and still.stamp["policy"] == "claim_unavailable"
    )
    frozen[0] = frozen[0] + timedelta(seconds=cfg.approvals.standing_window_s + 1)
    after = C.operator_gate(log, cfg, canon)
    assert not after.ok and after.ask_id and "requested" in after.message  # a person is asked
    assert after.ask_id != first.ask_id  # a NEW, still-unanswered ask, not the opening one


def test_standing_verbs_scopes_what_the_window_covers(proj, monkeypatch):
    _frozen_clock(monkeypatch)
    log = EventLog(proj, "bot")
    canon = Canon(verb="complete.force", agent="bot", item="T1", state="s")
    cfg = _cfg(policy=["complete_unavailable"], policy_max_uses=99, standing_verbs=["claim.force"])
    assert not C.operator_gate(log, cfg, canon).ok  # complete.force is outside the window's scope


def test_skip_after_unavailable_is_window_gated_too(proj, monkeypatch):
    from datetime import timedelta

    frozen = _frozen_clock(monkeypatch)
    log = EventLog(proj, "bot")
    cfg = _cfg(policy=["skip_after_unavailable"], policy_max_uses=99)
    assert C.operator_gate(log, cfg, _canon(proj, log)).ok  # opens the window
    frozen[0] = frozen[0] + timedelta(seconds=cfg.approvals.standing_window_s + 1)
    after = C.operator_gate(log, cfg, _canon(proj, log))
    assert not after.ok and after.ask_id  # skip_after_unavailable lapsed too


def _complete(proj, log, state: str, item: str = "T1"):
    return Canon(verb="complete.force", agent="bot", item=item, state=state)


def test_after_n_refusals_the_next_is_auto_approved_and_notified(proj):
    log = EventLog(proj, "bot")
    cfg = _cfg(auto_approve_after_retries=2)
    last = None
    for s in ("r1", "r2"):  # two DISTINCT digests -> two asks the operator never answered
        last = _complete(proj, log, state=s)
        got = C.operator_gate(log, cfg, last)
        assert not got.ok and got.ask_id  # filed + refused, essentially: a person was asked
    canon = _complete(proj, log, state="r3")
    got = C.operator_gate(log, cfg, canon)
    assert got.ok and got.stamp["via"] == "policy" and got.stamp["policy"] == "auto-after-retries"
    row = asks_of(_state(proj))[got.ask_id]
    assert row["policy"] is True and row["answered_by"] == "policy:auto-after-retries"
    assert row["digest"] == canon.digest()  # the request's digest is preserved
    assert row["notify"] == 2  # the notification record: the refused count that triggered it
    rows = AU.rows(_state(proj), cfg)
    assert any(r["approver"] == "policy:auto-after-retries" and r["notify"] for r in rows)
    assert any(
        "auto-after-retries" in f and "refusal" in f for f in AU.findings(_state(proj), cfg)
    )


def test_zero_retry_threshold_never_auto_approves(proj):
    log = EventLog(proj, "bot")
    assert Config().approvals.auto_approve_after_retries == 0  # ships OFF
    for s in ("a", "b", "c"):
        assert not C.operator_gate(log, Config(), _complete(proj, log, state=s)).ok
    assert not any(r["policy"] for r in asks_of(_state(proj)).values())


def test_the_retry_rule_fires_on_count_alone_not_the_window_or_the_policy_list(proj, monkeypatch):
    _frozen_clock(monkeypatch)
    log = EventLog(proj, "bot")
    cfg = _cfg(auto_approve_after_retries=1)
    assert cfg.approvals.policy == []  # the retry rule is NOT a `policy` list entry
    assert not C.operator_gate(log, cfg, _complete(proj, log, state="r1")).ok  # asked, not answered
    got = C.operator_gate(log, cfg, _complete(proj, log, state="r2"))
    assert got.ok and got.stamp["policy"] == "auto-after-retries"

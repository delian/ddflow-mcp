"""OD-queue: the operator queue is a pure, ranked, honest function of state, config and now."""

from __future__ import annotations

from pathlib import Path

import pytest

from ddflow.config import Config
from ddflow.core import clock
from ddflow.core.handlers.asks import asks_of
from ddflow.core.model import DONE, OPEN, GateRecord, State, fold
from ddflow.core.records import Decision, Item
from ddflow.services.digest import queue as Q
from ddflow.services.digest import sources as S
from ddflow.services.digest.entry import Facts, dependants, severity_at

T0 = "2026-10-20T10:00:00+00:00"
NOW = clock.epoch("2026-10-20T10:00:00+00:00")
H = 3600


def at(hours_ago: float) -> str:
    return clock.iso_at(NOW - hours_ago * H)


def item(st: State, id: str, **kw) -> Item:
    it = Item(id=id, kind=kw.pop("kind", "task"), **kw)
    st.items[id] = it
    return it


def ask(
    st: State, id: str, hours: float, *, verb: str = "", by: str = "agent-a", subject: str = ""
):
    asks_of(st)[id] = {
        "id": id, "type": "approval", "subject": subject, "text": "SECRET REASON",
        "digest": "d", "options": [], "expires_at": clock.iso_at(NOW + 99 * H),
        "verb": verb, "tier": "T2", "min_level": 1, "uses": 1, "use_ttl_s": 0, "canon": "",
        "requested_by": by, "requested_at": at(hours), "state": "pending",
    }  # fmt: skip


def build(st: State, **kw):
    return Q.build(st, Config(), NOW, **kw)


def test_empty_state_is_an_empty_queue_with_its_gaps_named():
    q = build(State())
    assert q.entries == ()
    assert any(g.startswith("agent_failed") for g in q.gaps)
    assert any(g.startswith("lease") for g in q.gaps)  # health not checked


def test_every_source_has_a_collector_that_resolves_and_runs():
    for s in S.SOURCES:
        fn = getattr(Q, f"collect_{s.kind}")
        assert fn(State(), Config(), NOW, Facts()) == []
    assert set(Q.UNCOLLECTED) <= {s.kind for s in S.SOURCES}


def test_a_pending_approval_names_what_why_default_answer_and_chat_route():
    st = State()
    item(st, "T1", title="t")
    ask(
        st, "Q1", 2, verb="gate.skip.failed_review", subject="cmd:gate.skip.failed_review:T1:critic"
    )
    (e,) = build(st).entries
    assert e.kind == "gate_skip" and e.owner == "operator"
    assert "T1" in e.what and "critic" not in e.what
    assert e.blocks == ("T1",) and e.agents == ("agent-a",)
    assert e.command == "ddflow approvals approve Q1"
    assert (
        e.chat == "ddflow approvals ask Q1  (the exact AskUserQuestion payload; show it unaltered)"
    )
    assert e.default.startswith("deny") and e.age_s == 2 * H
    assert "SECRET REASON" not in e.what + e.why  # an agent's words are never copied in


def test_an_unlisted_verb_is_a_plain_approval_and_a_question_has_no_verb():
    st = State()
    ask(st, "Q1", 1, verb="grant.add", subject="cmd:grant.add:X:")
    ask(st, "Q2", 1)
    kinds = sorted(e.kind for e in build(st).entries)
    assert kinds == ["approval", "question"]


def test_severity_rises_with_age_by_the_ladder_and_never_resets():
    cfg = Config()
    assert severity_at("approval", 0, cfg) == "nudge"
    assert severity_at("approval", 1 * H, cfg) == "needs"
    assert severity_at("approval", 24 * H, cfg) == "urgent"
    assert severity_at("chore", 0, cfg) == "nudge"
    assert severity_at("red_main", 5 * H, cfg) == "needs"
    assert severity_at("red_main", 6 * H, cfg) == "urgent"
    cfg.digest.escalate_after = "10m, 20m"
    assert severity_at("approval", 20 * 60, cfg) == "urgent"


def test_ranking_is_severity_then_impact_then_age_and_is_deterministic():
    st = State()
    item(st, "A")
    item(st, "B")
    item(st, "C", needs=["A"])
    item(st, "D", needs=["C"])
    ask(st, "Q-old", 30, verb="grant.add", subject="cmd:grant.add:B:")
    ask(st, "Q-new", 0.1, verb="grant.add", subject="cmd:grant.add:A:")
    ask(st, "Q-mid", 3, verb="grant.add", subject="cmd:grant.add:A:")
    ids = [e.id for e in build(st).entries]
    assert ids == ["approval:Q-old", "approval:Q-mid", "approval:Q-new"]
    assert ids == [e.id for e in build(st).entries]
    mid = next(e for e in build(st).entries if e.id == "approval:Q-mid")
    assert mid.impact == 3  # A itself, C and D behind it


def test_dependants_follow_needs_transitively_and_skip_done_items():
    st = State()
    item(st, "A")
    item(st, "B", needs=["A"])
    item(st, "C", needs=["B"], state=DONE)
    item(st, "D", needs=["B"])
    assert dependants(st, ["A"]) == {"B", "D"}


def test_a_decide_task_is_listed_only_when_what_it_needs_is_done():
    st = State()
    item(st, "R", state=DONE)
    item(st, "X", state=OPEN)
    item(
        st,
        "D1",
        kind="doc",
        title="DECIDE: pick",
        needs=["R"],
        created_at=at(5),
        body="Q1\nDefault: option C",
    )
    item(st, "D2", kind="doc", title="DECIDE: later", needs=["X"], created_at=at(5))
    item(st, "W", needs=["D1"])
    es = build(st).entries
    assert [e.id for e in es] == ["decide:D1"]
    assert es[0].default == "Default: option C" and es[0].impact == 2  # D1 and W


def test_proposed_decisions_and_finished_phases():
    st = State()
    st.decisions["D-x"] = Decision(id="D-x", title="t", status="proposed", at=at(2))
    st.decisions["D-y"] = Decision(id="D-y", title="t", status="accepted", at=at(2))
    item(st, "P", kind="phase")
    item(st, "P1", parent="P", state=DONE, completed_at=at(3))
    es = build(st).entries
    assert sorted(e.id for e in es) == ["decision:D-x", "phase_close:P"]
    assert {e.id: e.owner for e in es}["phase_close:P"] == "lead"


def test_red_main_counts_from_the_first_failure_of_the_streak():
    st = State()
    ok = {"stage": "merge", "ok": True, "checks": [], "sha": "a"}
    bad = {"stage": "merge", "ok": False, "checks": ["ruff"], "sha": "b"}
    st.ci_results += [{**ok, "at": at(10)}, {**bad, "at": at(8)}, {**bad, "at": at(7), "sha": "c"}]
    st.ci_results.append({"stage": "gate", "ok": True, "at": at(1), "checks": [], "sha": "z"})
    (e,) = build(st).entries
    assert e.kind == "red_main" and e.age_s == 8 * H and e.severity == "urgent"
    assert "ruff" in e.why and e.detail["failures_in_a_row"] == 2
    st.ci_results.append({**ok, "at": at(0.5)})
    assert build(st).entries == ()


def test_credential_gate_and_spotcheck_and_lease_facts():
    st = State()
    it = item(st, "T")
    it.gates["critic"] = GateRecord(
        gate="critic",
        outcome="unavailable",
        at=at(4),
        by="a",
        reason="HTTP 401 unauthorized: KIMI_API_KEY",
    )
    it.gates["ci"] = GateRecord(gate="ci", outcome="unavailable", at=at(4), reason="network down")
    es = build(st).entries
    assert [e.id for e in es] == ["credential:T.critic"]

    class F:
        kind, item, holder, age_s, detail, fix = (
            "stalled",
            "T",
            "impl-x",
            7200.0,
            "no progress",
            "ddflow health",
        )

    q = build(st, facts=Facts(health=(F(),)))
    lease = next(e for e in q.entries if e.kind == "lease")
    assert lease.owner == "lead" and lease.agents == ("impl-x",) and lease.age_s == 7200
    assert not any(g.startswith("lease") for g in q.gaps)
    assert build(st).for_operator()[0].kind == "credential"
    assert all(e.owner == "operator" for e in q.for_operator())


def test_entries_serialize_without_loss():
    st = State()
    ask(st, "Q1", 1, verb="grant.add", subject="cmd:grant.add:X:")
    d = build(st).entries[0].as_dict()
    assert {
        "id",
        "kind",
        "owner",
        "what",
        "why",
        "since",
        "default",
        "answer",
        "command",
        "chat",
        "blocks",
        "impact",
        "severity",
        "age_s",
    } <= set(d)


@pytest.mark.skipif(not Path(".ddflow/events").exists(), reason="no event log in this checkout")
def test_replay_over_the_real_log_is_sane_and_deterministic():
    from ddflow.infra.log import EventLog

    root = Path(__file__).resolve().parents[1]
    st = fold(EventLog(root, log_cfg=Config.load(root).log).read_all(), strict=False)
    a = Q.build(st, Config(), NOW + 1e6)
    b = Q.build(st, Config(), NOW + 1e6)
    assert [e.as_dict() for e in a.entries] == [e.as_dict() for e in b.entries]
    assert len({e.id for e in a.entries}) == len(a.entries)
    assert all(e.severity in S.SEVERITIES for e in a.entries)

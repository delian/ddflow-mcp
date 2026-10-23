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


def test_equal_severity_ranks_by_impact_before_age():
    st = State()
    item(st, "A")
    item(st, "B")
    item(st, "C", needs=["B"])
    ask(st, "Q-a", 0.5, verb="grant.add", subject="cmd:grant.add:A:")  # older, impact 1
    ask(st, "Q-b", 0.2, verb="grant.add", subject="cmd:grant.add:B:")  # newer, impact 2
    assert {e.severity for e in build(st).entries} == {"nudge"}
    assert [e.id for e in build(st).entries] == ["approval:Q-b", "approval:Q-a"]


def test_a_bare_string_block_is_one_item_not_its_letters():
    from ddflow.services.digest.entry import make

    st = State()
    item(st, "item1")
    e = make(
        st,
        Config(),
        NOW,
        "approval",
        "k",
        what="w",
        why="y",
        since=at(1),
        default="d",
        blocks="item1",
        agents="bob",
    )
    assert e.blocks == ("item1",) and e.agents == ("bob",) and e.impact == 1


def test_plural_and_expired_credentials_are_recognised():
    st = State()
    it = item(st, "T")
    it.gates["critic"] = GateRecord(
        gate="critic", outcome="unavailable", at=at(1), reason="expired credentials"
    )
    assert [e.kind for e in build(st).entries] == ["credential"]


def test_a_negative_red_main_wait_is_refused():
    from ddflow.config_sections.digest import _age_problem

    assert _age_problem("-1h") and _age_problem("6h") == ""


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
    assert d["id"] == "approval:Q1" and d["kind"] == "approval" and d["owner"] == "operator"
    assert d["what"] == "agent-a asks for `grant.add` on X" and "person-only" in d["why"]
    assert d["since"] == at(1) and d["age_s"] == H and d["severity"] == "needs"
    assert d["default"].startswith("deny") and d["command"] == "ddflow approvals approve Q1"
    assert d["answer"] and d["chat"] and d["blocks"] == [] and d["impact"] == 0


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(
    not (ROOT / ".ddflow" / "events").exists(), reason="no event log in this checkout"
)
def test_replay_over_the_real_log_is_sane_and_deterministic():
    from ddflow.infra.log import EventLog

    events = EventLog(ROOT, log_cfg=Config.load(ROOT).log).read_all()
    st = fold(events, strict=False)
    when = max(clock.epoch(ev.ts) for ev in events)  # the queue as of the log's newest event
    a = Q.build(st, Config(), when)
    b = Q.build(st, Config(), when)
    assert [e.as_dict() for e in a.entries] == [e.as_dict() for e in b.entries]
    assert len({e.id for e in a.entries}) == len(a.entries)
    assert all(e.severity in S.SEVERITIES for e in a.entries)
    # counts agree with the log, so an empty queue over a busy log cannot pass
    from ddflow.services.ask import engine as E
    from ddflow.services.digest import collect_asks as CA

    waiting = E.pending(st, when)
    by_ask = sum(
        e.kind in {CA.kind_of(r) for r in waiting} and "ask" in e.detail for e in a.entries
    )
    assert by_ask == len(waiting)
    open_decides = [
        i for i in st.items.values()
        if i.kind == "doc" and i.state == OPEN and i.title.upper().startswith("DECIDE")
        and all(st.items[n].state == DONE for n in i.needs if n in st.items)
    ]  # fmt: skip
    assert sum(e.kind == "decide" for e in a.entries) == len(open_decides)
    assert sum(e.kind == "decision" for e in a.entries) == sum(
        d.status == "proposed" for d in st.decisions.values()
    )


def test_an_info_kind_starts_below_nudge_and_still_rises():
    cfg = Config()
    assert severity_at("phase_close", 0, cfg) == "info"
    assert severity_at("phase_close", 1 * H, cfg) == "needs"
    assert severity_at("phase_close", 24 * H, cfg) == "urgent"


def test_a_credential_entry_never_copies_the_gate_reason():
    st = State()
    it = item(st, "T")
    it.gates["critic"] = GateRecord(
        gate="critic", outcome="unavailable", at=at(1), reason="invalid api key: sk-abc123"
    )
    (e,) = build(st).entries
    assert "sk-abc123" not in repr(e.as_dict())


def test_a_question_shows_its_quoted_text_and_how_to_answer():
    st = State()
    ask(st, "Q9", 1)
    asks_of(st)["Q9"]["text"] = "ship it\nnow?"
    (e,) = build(st).entries
    assert (
        e.kind == "question" and "ship it now?" in e.what and e.detail["question"] == "ship it now?"
    )
    assert e.command == "ddflow approvals show Q9" and e.chat.startswith(
        "ddflow digest --ask question:Q9"
    )
    assert e.default == "answer in your own words" and "answer the question" in e.answer
    asks_of(st)["Q9"]["options"] = ["yes", "no"]
    assert Q.build(st, Config(), NOW).entries[0].default == "one of: yes, no"


def test_an_abandoned_item_is_not_awaiting_a_spot_check():
    from ddflow.core.model import ABANDONED

    st = State()
    it = item(st, "T", state=ABANDONED)
    it.gates["critic"] = GateRecord(
        gate="critic",
        outcome="passed",
        at=at(1),
        evidence={"passed_on_refutation": {"refuted": 1, "confirmed": 0, "rounds": 1}},
    )
    assert build(st).entries == ()
    it.state = OPEN
    assert [e.kind for e in build(st).entries] == ["gate_spotcheck"]  # the same gate, on live work


def test_quoted_text_is_masked_and_cut_by_the_knob():
    st = State()
    ask(st, "Q9", 1)
    asks_of(st)["Q9"]["text"] = (
        "deploy with token ghp_" + "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8 " + "x" * 600
    )
    cfg = Config()
    (e,) = Q.build(st, cfg, NOW).entries
    assert "ghp_a1b2" not in repr(e.as_dict()) and len(e.detail["question"]) == 400
    cfg.digest.text_chars = 20
    assert len(Q.build(st, cfg, NOW).entries[0].detail["question"]) == 20


def test_a_decide_default_line_is_masked_too():
    st = State()
    item(
        st,
        "D1",
        kind="doc",
        title="DECIDE: k",
        created_at=at(1),
        body="default: use ghp_" + "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8",
    )
    (e,) = build(st).entries
    assert "ghp_a1b2" not in e.default


def test_a_question_with_a_verb_is_still_a_question_and_its_id_is_quoted():
    st = State()
    ask(st, "weird id", 1, verb="ask", subject="cmd:ask:X:")
    asks_of(st)["weird id"]["type"] = "question"
    (e,) = build(st).entries
    assert e.kind == "question" and e.default == "answer in your own words"
    assert e.command == "ddflow approvals show 'weird id'"


def test_a_lease_finding_detail_is_masked():
    class F:
        kind, item, holder, age_s, fix = "stalled", "", "h", 10.0, "ddflow health"
        detail = "token ghp_" + "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8"

    (e,) = [x for x in build(State(), facts=Facts(health=(F(),))).entries if x.kind == "lease"]
    assert "ghp_a1b2" not in e.why

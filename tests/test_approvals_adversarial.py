"""RA-e2e: an agent that forges, replays, widens, retargets or self-approves gets nothing.

Each case runs the hostile move and then asserts both that it was refused and that no
operator-only act happened. The honest limit (docs/ddflow/decisions/remote-approvals.md): on one
uid a terminal answer is the log's word, so a hand-appended terminal answer reaches L1 and no
higher; what needs more than L1 is refused here."""

from __future__ import annotations

import json
import sys
import time
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
from ddflow.services.approvals import consume as C
from ddflow.services.approvals import harness as H
from ddflow.services.approvals import policy as PO
from ddflow.services.approvals import request as R
from ddflow.services.approvals.canon import Canon
from ddflow.services.ask import engine as E

T0 = time.time()
VERB_T2 = "gate.skip.failed_review"
VERB_T3 = "config.reviewer_command"


@pytest.fixture(autouse=True)
def _a_person(monkeypatch):
    for var in ("DDFLOW_AGENT", *AP.HARNESS_MARKERS):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def log(repo):
    return EventLog(repo, "bot")


def _state(log):
    return fold(log.read_all(), strict=False)


def _canon(verb=VERB_T2, **over):
    base = {"verb": verb, "agent": "bot", "item": "T1", "target": "critic", "state": "s1"}
    base.update(over)
    return Canon(**base)


def _file(log, cfg, canon=None):
    return R.file(log, cfg, _state(log), canon or _canon(), reason="r", now=T0)


def _forge(log, filed, **over):
    data = {"id": filed.id, "outcome": "approved", "digest": filed.digest, "by": "me",
            "human": True}  # fmt: skip
    data.update(over)
    log.append("ask.answered", filed.id, data)


# -- the agent cannot answer, revoke, mark or widen -------------------------------------------


@pytest.mark.parametrize("act", ["approved", "denied"])
def test_an_agent_identity_cannot_answer_by_any_door(log, act):
    cfg = Config()
    filed = _file(log, cfg)
    for who in ("bot", "other-bot", "root", "operator"):  # naming a person's role is no proof
        actor = A.resolve_actor(cfg, who, requested_agent=who)
        with pytest.raises(E.AskRefused):
            E.answer(log, cfg, filed.id, act, actor=actor, digest=filed.digest, now=T0)
    assert asks_of(_state(log))[filed.id]["state"] == "pending"


def test_an_agent_cannot_mark_its_own_ask_bot_answerable(log):
    cfg = Config()
    me = A.resolve_actor(cfg, "bot", requested_agent="bot")
    with pytest.raises(E.AskRefused, match="approver"):
        E.request(log, cfg, subject="s", digest="d", bot_answerable=True, actor=me)
    with pytest.raises(E.AskRefused, match="approver"):
        E.request(log, cfg, subject="s", digest="d", bot_answerable=True, actor=None)
    assert asks_of(_state(log)) == {}


def test_an_agent_cannot_revoke_withdraw_someone_elses_or_answer_the_cli(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "t.py")
    run_cli(repo, "gate", "record", "T1", "critic", "--outcome", "failed", "--reason", "x")
    code, _o, _err = run_cli(repo, "gate", "skip", "T1", "critic", "--reason", "r", agent="bot")
    assert code == 3
    ask_id = next(iter(asks_of(fold(EventLog(repo, "p").read_all(), strict=False))))
    digest = asks_of(fold(EventLog(repo, "p").read_all(), strict=False))[ask_id]["digest"]
    for argv in (
        ("approvals", "approve", ask_id, "--digest", digest[:12]),
        ("approvals", "deny", ask_id),
        ("approvals", "revoke", ask_id),
    ):
        for agent in ("bot", "mallory"):
            assert run_cli(repo, *argv, agent=agent)[0] == 3, argv
    row = asks_of(fold(EventLog(repo, "p").read_all(), strict=False))[ask_id]
    assert row["state"] == "pending"
    # another agent cannot take the asker's request back either
    other = A.resolve_actor(Config(), "mallory", requested_agent="mallory")
    with pytest.raises(E.AskRefused, match="may withdraw"):
        E.withdraw(EventLog(repo, "mallory"), ask_id, actor=other)


# -- forging the log -------------------------------------------------------------------------


@pytest.mark.parametrize("source", ["terminal", "credential", "named", "signed", "chat", "harness"])
@pytest.mark.parametrize("claimed", [(0, ""), (2, "harness"), (3, "chat"), (4, "signed")])
def test_a_hand_appended_answer_never_reaches_a_level_it_cannot_prove(log, claimed, source):
    """An agent appends `ask.answered` with a high level it cannot back. For a VERB_T3 act (needs
    L4) it is refused whatever it claims; with no recorded source it is refused outright."""
    cfg = Config()
    t3 = _canon(VERB_T3, item="", target="")
    filed = _file(log, cfg, t3)
    level, via = claimed
    _forge(log, filed, source=source, level=level, via=via)
    got = C.operator_gate(log, cfg, t3, approval_id=filed.id, now=T0 + 1)
    assert not got.ok, (claimed, source)
    assert asks_of(_state(log))[filed.id]["used"] == 0


def test_a_hand_appended_answer_without_a_source_approves_nothing_at_any_tier(log):
    cfg = Config()
    for verb in (VERB_T2, VERB_T3):
        canon = _canon(
            verb, item="" if verb == VERB_T3 else "T1", target="" if verb == VERB_T3 else "critic"
        )
        filed = _file(log, cfg, canon)
        _forge(log, filed)  # no source, no level
        got = C.operator_gate(log, cfg, canon, approval_id=filed.id, now=T0 + 1)
        assert not got.ok and "source" in got.message


def test_a_hand_appended_answer_for_another_digest_or_command_is_refused(log):
    cfg = Config()
    filed = _file(log, cfg)
    _forge(log, filed, source="terminal", digest="0" * 64)
    assert not C.operator_gate(log, cfg, _canon(), approval_id=filed.id, now=T0 + 1).ok
    other = _file(log, cfg, _canon(item="T9"))
    _forge(log, other, source="terminal")  # genuine-looking answer, spent on a different command
    assert not C.operator_gate(log, cfg, _canon(item="T1"), approval_id=other.id, now=T0 + 1).ok


def test_replaying_a_spent_approval_cannot_be_undone_by_appending_events(log):
    cfg = Config()
    filed = _file(log, cfg)
    person = A.resolve_actor(cfg, "me")
    E.answer(log, cfg, filed.id, "approved", actor=person, digest=filed.digest, now=T0)
    assert C.operator_gate(log, cfg, _canon(), approval_id=filed.id, now=T0 + 1).ok
    assert asks_of(_state(log))[filed.id]["used"] == 1
    # the hostile appends: the same id requested again, answered again, a fake fresh use
    log.append("ask.requested", filed.id, {"id": filed.id, "verb": VERB_T2, "digest": filed.digest,
                                           "uses": 9, "min_level": 1})  # fmt: skip
    _forge(log, filed, source="terminal")
    log.append("ask.consumed", filed.id, {"id": filed.id, "by": "x", "used": 0})
    for n in range(3):
        got = C.operator_gate(log, cfg, _canon(), approval_id=filed.id, now=T0 + 2 + n)
        assert not got.ok, n
    row = asks_of(_state(log))[filed.id]
    assert row["uses"] == 1 and row["used"] == 1  # the first request owns the id and the count


# -- retargeting and widening ------------------------------------------------------------------


@pytest.mark.parametrize(
    "change",
    [
        {"item": "VERB_T2"},
        {"target": "rubber_duck"},
        {"state": "s2"},
        {"verb": "complete.force"},
        {"verb": VERB_T3},
    ],
)
def test_an_approval_does_not_stretch_to_a_changed_command(log, change):
    cfg = Config()
    filed = _file(log, cfg)
    E.answer(log, cfg, filed.id, "approved", actor=A.resolve_actor(cfg, "me"),
             digest=filed.digest, now=T0)  # fmt: skip
    got = C.operator_gate(log, cfg, _canon(**change), approval_id=filed.id, now=T0 + 1)
    assert not got.ok
    assert asks_of(_state(log))[filed.id]["used"] == 0  # the refusal spent nothing


def test_an_expired_request_cannot_be_answered_or_spent_and_says_so(log):
    cfg = Config()
    person = A.resolve_actor(cfg, "me")
    filed = _file(log, cfg)
    late = T0 + cfg.agent.ask_ttl_s + 1
    with pytest.raises(E.AskRefused, match="expired"):
        E.answer(log, cfg, filed.id, "approved", actor=person, digest=filed.digest, now=late)
    got = C.operator_gate(log, cfg, _canon(), approval_id=filed.id, now=late)
    assert not got.ok and "expired" in got.message
    # control: the same request answered in time is spendable, so only the clock differs
    fresh = _file(log, cfg, _canon(item="F"))
    E.answer(log, cfg, fresh.id, "approved", actor=person, digest=fresh.digest, now=T0)
    assert C.operator_gate(log, cfg, _canon(item="F"), approval_id=fresh.id, now=T0 + 1).ok


def test_a_denied_withdrawn_or_missing_id_is_never_an_approval(log):
    cfg = Config()
    person = A.resolve_actor(cfg, "me")
    denied = _file(log, cfg, _canon(item="D"))
    E.answer(log, cfg, denied.id, "denied", actor=person, digest=denied.digest, now=T0)
    withdrawn = _file(log, cfg, _canon(item="W"))
    E.withdraw(log, withdrawn.id, actor=person, now=T0)
    pending = _file(log, cfg, _canon(item="P"))
    for filed, item in ((denied, "D"), (withdrawn, "W"), (pending, "P")):
        got = C.operator_gate(log, cfg, _canon(item=item), approval_id=filed.id, now=T0 + 1)
        assert not got.ok, item
    for junk in ("", "Q", "Qzzzzzzzzzz", "../../x", "Q" + "0" * 10, "request\n--approval x"):
        assert not C.operator_gate(log, cfg, _canon(), approval_id=junk, now=T0 + 1).ok, junk


def test_a_t3_act_refuses_a_level_one_answer_and_a_chat_answer_without_the_opt_in(log, tmp_path):
    cfg = Config()
    cfg.approvals.transcript_dirs = [str(tmp_path / "projects")]
    t3 = _canon(VERB_T3, item="", target="")
    filed = _file(log, cfg, t3)
    E.answer(log, cfg, filed.id, "approved", actor=A.resolve_actor(cfg, "me"),
             digest=filed.digest, now=T0)  # fmt: skip  # a person at the terminal: L1
    got = C.operator_gate(log, cfg, t3, approval_id=filed.id, now=T0 + 1)
    assert not got.ok and "L4" in got.message
    assert asks_of(_state(log))[filed.id]["used"] == 0
    # the person typing it in chat does not lift it unless the machine-local opt-in is on
    other = _file(log, cfg, _canon(VERB_T3, item="x", target=""))
    d = Path(cfg.approvals.transcript_dirs[0]) / "slug"
    d.mkdir(parents=True)
    typed = {"type": "user", "uuid": "U1", "timestamp": "2026-10-24T10:00:00Z",
             "origin": {"kind": "human"},
             "message": {"content": f"approve {other.id} {other.digest[:8]}"}}  # fmt: skip
    (d / "s.jsonl").write_text(json.dumps(typed) + "\n")
    ok, message = H.attest(log, cfg, other.id)
    assert not ok and "accept_chat_for_t3" in message
    assert not C.operator_gate(
        log, cfg, _canon(VERB_T3, item="x", target=""), approval_id=other.id
    ).ok


def test_auto_approval_never_reaches_a_higher_tier(repo, log, monkeypatch):
    """A standing rule that matches its verb answers a VERB_T2 act and is refused for a VERB_T3 one:
    the contrast proves the rule is live and the tier is what stops it."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "t.py")
    cfg = Config()
    cfg.approvals.standing_verbs = [
        *cfg.approvals.standing_verbs,
        VERB_T3,
    ]  # in scope: only the tier stops it
    always = lambda it, target, c: True  # noqa: E731
    monkeypatch.setitem(PO.RULES, "t2rule", PO.Rule("t2rule", VERB_T2, "x", always))
    monkeypatch.setitem(PO.RULES, "t3rule", PO.Rule("t3rule", VERB_T3, "x", always))
    cfg.approvals.policy = ["t2rule", "t3rule"]
    ok = C.operator_gate(log, cfg, _canon(), reason="r", now=T0)
    assert ok.ok and ok.stamp["via"] == "policy"
    t3 = _canon(VERB_T3, target="")  # item T1 exists, the rule matches, the window covers the verb
    got = C.operator_gate(log, cfg, t3, reason="r", now=T0)
    assert not got.ok and got.ask_id
    row = asks_of(_state(log))[got.ask_id]
    assert row["state"] == "pending" and not row["policy"] and row["used"] == 0

"""RA-consume: spending an approval at a refusal site, and the attacks on it.

Forge, replay, widen, self-approve, expire, wrong digest: each is refused by the check the
agent cannot satisfy by wording. The person-only answer path itself (`engine.answer`) is
unchanged; these tests only ever answer as a person at a terminal.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time

import pytest

from ddflow.config import Config
from ddflow.core import clock
from ddflow.core.digest import content_digest
from ddflow.core.handlers.asks import asks_of
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import actors as A
from ddflow.services.approvals import consume as C
from ddflow.services.approvals import request as R
from ddflow.services.approvals.canon import Canon
from ddflow.services.ask import engine as E

T0 = time.time()  # answers are stamped with the real clock: the use window is read against it
VERB = "gate.skip.failed_review"


@pytest.fixture(autouse=True)
def _a_person(monkeypatch):
    for var in ("DDFLOW_AGENT", "CLAUDECODE"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def log(repo):
    return EventLog(repo, "asker")


def _state(log):
    return fold(log.read_all(), strict=False)


def _canon(**over):
    base = {"verb": VERB, "agent": "asker", "item": "fix-X", "target": "critic", "state": "s1"}
    base.update(over)
    return Canon(**base)


def _request(log, cfg, canon=None, now=T0):
    return R.file(log, cfg, _state(log), canon or _canon(), reason="r", now=now)


def _approve(log, cfg, filed, now=T0, outcome="approved", level=0):
    person = A.resolve_actor(cfg, "me")
    E.answer(log, cfg, filed.id, outcome, actor=person, digest=filed.digest, level=level, now=now)


def _approved(log, cfg, canon=None, now=T0):
    filed = _request(log, cfg, canon, now)
    _approve(log, cfg, filed, now)
    return filed


def test_a_person_approves_and_the_agent_spends_it_exactly_once(log):
    cfg = Config()
    filed = _approved(log, cfg)
    got = C.operator_gate(log, cfg, _canon(), approval_id=filed.id, now=T0 + 10)
    assert got.ok and got.ask_id == filed.id
    assert got.stamp["approved_by"] == "me" and got.stamp["proxy"] == "asker"
    assert got.stamp["level"] == 1 and got.stamp["digest"] == filed.digest
    row = asks_of(_state(log))[filed.id]
    assert row["used"] == 1 and row["consumed"][0]["by"] == "asker"
    replay = C.operator_gate(log, cfg, _canon(), approval_id=filed.id, now=T0 + 11)
    assert not replay.ok and "spent" in replay.message
    assert asks_of(_state(log))[filed.id]["used"] == 1


def test_without_an_approval_the_request_is_filed_and_the_route_named(log):
    cfg = Config()
    first = C.operator_gate(log, cfg, _canon(), reason="reviewer is down", now=T0)
    assert not first.ok and first.ask_id
    assert f"--approval {first.ask_id}" in first.message and "requested" in first.message
    again = C.operator_gate(log, cfg, _canon(), now=T0 + 1)
    assert again.ask_id == first.ask_id and "still waiting" in again.message
    assert len(asks_of(_state(log))) == 1


def test_auto_request_off_files_nothing(log):
    cfg = Config()
    cfg.approvals.auto_request = False
    got = C.operator_gate(log, cfg, _canon(), now=T0)
    assert not got.ok and not got.ask_id and asks_of(_state(log)) == {}


def test_an_unanswered_denied_or_withdrawn_request_cannot_be_spent(log):
    cfg = Config()
    waiting = _request(log, cfg)
    got = C.operator_gate(log, cfg, _canon(), approval_id=waiting.id, now=T0 + 1)
    assert not got.ok and "not answered" in got.message
    denied = _request(log, cfg, _canon(item="fix-Y"))
    _approve(log, cfg, denied, outcome="denied")
    got = C.operator_gate(log, cfg, _canon(item="fix-Y"), approval_id=denied.id, now=T0 + 1)
    assert not got.ok and "denied" in got.message
    gone = _request(log, cfg, _canon(item="fix-Z"))
    E.withdraw(log, gone.id, actor=A.resolve_actor(cfg, "me"), now=T0)
    got = C.operator_gate(log, cfg, _canon(item="fix-Z"), approval_id=gone.id, now=T0 + 1)
    assert not got.ok and "withdrawn" in got.message


def test_an_approval_cannot_be_widened_to_another_item_gate_agent_or_verb(log):
    cfg = Config()
    filed = _approved(log, cfg)
    for other in (
        _canon(item="fix-OTHER"),
        _canon(target="ci"),
        _canon(args={"outcome": "passed"}),
        _canon(verb="grant.add"),
    ):
        got = C.operator_gate(log, cfg, other, approval_id=filed.id, now=T0 + 1)
        assert not got.ok, other
    assert asks_of(_state(log))[filed.id]["used"] == 0  # none of them spent it


def test_an_approval_binds_the_action_not_the_agent(log, repo):
    """B86be969daf: the digest binds the command (verb, item, target, args, state), never the
    agent, so a different agent re-running the SAME action still cannot decide for itself --
    and yet a WIDENED action (a different item/gate/args/verb) is refused even by a same-named
    process. The spend is recorded under whoever ran it."""
    cfg = Config()
    filed = _approved(log, cfg)
    thief = EventLog(repo, "thief")
    got = C.spend(thief, _canon(agent="thief"), filed.id, now=T0 + 1)
    assert got.ok is True  # the same action (item+target+state), a different agent: allowed
    assert asks_of(_state(log))[filed.id]["consumed"][0]["by"] == "thief"


def test_a_successor_lane_spends_a_predecessors_unspent_approval(log, repo):
    """B86be969daf: a lane files, is approved, then dies unnamed; a successor lane re-claiming
    the SAME item+state may spend the already-granted answer instead of the answer being
    owner-gated to the dead agent and expiring unused."""
    cfg = Config()
    filed = _approved(log, cfg)  # requested by "asker" (the dead lane)
    successor = EventLog(repo, "successor")
    # the same verb/item/target/state, filed by a DIFFERENT agent: the digest matches and
    # the answer is spent, so the successor is not stranded behind a dead agent's answer.
    got = C.operator_gate(
        successor, cfg, _canon(agent="successor"), approval_id=filed.id, now=T0 + 1
    )
    assert got.ok and asks_of(_state(log))[filed.id]["used"] == 1
    assert asks_of(_state(log))[filed.id]["consumed"][0]["by"] == "successor"


def test_a_pre_fix_agent_bound_approval_is_still_spent_by_a_successor(log, repo):
    """B86be969daf migration: an approval filed BEFORE the fix stored its digest over the
    requesting agent (the legacy v1 record signed ``agent``). After the fix the actor is out of
    the digest, but the legacy digest must still match, or the already-granted answer strands.

    The legacy digest is built from a hardcoded pre-fix record literal (the actor present), not
    from the new ``canon.digest_named`` helper, so the test pins the old byte format itself."""
    cfg = Config()
    canon = _canon()  # agent="asker", the pre-fix requester
    legacy_rec = {
        "v": 1, "verb": canon.verb, "agent": "asker", "item": canon.item,
        "target": canon.target, "args": dict(canon.args), "state": canon.state,
    }  # fmt: skip
    legacy = content_digest(json.dumps(legacy_rec, sort_keys=True, separators=(",", ":")))
    asked = E.request(
        log, cfg, subject=canon.subject(), digest=legacy, command=R.command_of(canon, cfg)
    )  # fmt: skip
    E.answer(
        log, cfg, asked.id, "approved", actor=A.resolve_actor(cfg, "me"),
        digest=legacy, level=0, now=T0,
    )  # fmt: skip
    successor = EventLog(repo, "successor")
    got = C.operator_gate(
        successor, cfg, _canon(agent="successor"), approval_id=asked.id, now=T0 + 1
    )  # fmt: skip
    assert got.ok and asks_of(_state(log))[asked.id]["used"] == 1


def test_the_state_changing_since_the_request_needs_a_new_approval(log):
    cfg = Config()
    filed = _approved(log, cfg)
    got = C.operator_gate(log, cfg, _canon(state="s2"), approval_id=filed.id, now=T0 + 1)
    assert not got.ok and "other state" in got.message


def test_an_approval_expires_after_its_use_window(log):
    cfg = Config()
    filed = _approved(log, cfg)
    answered = clock.epoch(asks_of(_state(log))[filed.id]["answered_at"], default=0.0)
    late = answered + cfg.approvals.use_ttl_s + 1
    got = C.operator_gate(log, cfg, _canon(), approval_id=filed.id, now=late)
    assert not got.ok and "request a new approval" in got.message
    ok = C.operator_gate(
        log, cfg, _canon(), approval_id=filed.id, now=answered + cfg.approvals.use_ttl_s
    )
    assert ok.ok


def test_an_answer_after_the_request_window_is_refused_by_the_engine(log):
    cfg = Config()
    filed = _request(log, cfg)
    with pytest.raises(E.AskRefused, match="expired"):
        _approve(log, cfg, filed, now=T0 + cfg.agent.ask_ttl_s + 1)


def test_an_agent_cannot_answer_its_own_request(log):
    cfg = Config()
    filed = _request(log, cfg)
    me = A.resolve_actor(cfg, "asker", requested_agent="asker")
    with pytest.raises(E.AskRefused, match="person's act"):
        E.answer(log, cfg, filed.id, "approved", actor=me, digest=filed.digest, now=T0)
    got = C.operator_gate(log, cfg, _canon(), approval_id=filed.id, now=T0 + 1)
    assert not got.ok


def test_a_hand_appended_answer_does_not_approve(log):
    """Forge: the agent appends `ask.answered` itself. No recorded source, so nothing is approved."""
    cfg = Config()
    filed = _request(log, cfg)
    log.append(
        "ask.answered",
        filed.id,
        {"id": filed.id, "outcome": "approved", "digest": filed.digest, "by": "me", "human": True},
    )
    got = C.operator_gate(log, cfg, _canon(), approval_id=filed.id, now=T0 + 1)
    assert not got.ok and "source" in got.message


def test_a_forged_level_is_not_believed_without_a_channel_that_can_verify_it(log, monkeypatch):
    cfg = Config()
    t3 = _canon(verb="config.reviewer_command", item="", target="")
    filed = _request(log, cfg, t3)
    log.append(
        "ask.answered",
        filed.id,
        {
            "id": filed.id,
            "outcome": "approved",
            "digest": filed.digest,
            "by": "me",
            "human": True,
            "source": "terminal",
            "level": 4,
            "via": "signed",
        },
    )
    got = C.operator_gate(log, cfg, t3, approval_id=filed.id, now=T0 + 1)
    assert not got.ok and "needs L4" in got.message and "signed" in got.message
    monkeypatch.setitem(E.LEVEL_VERIFIERS, "signed", lambda row, cfg: True)
    assert C.operator_gate(log, cfg, t3, approval_id=filed.id, now=T0 + 2).ok
    # a verifier that says no means the claim is not believed
    filed2 = _request(log, cfg, _canon(verb="config.reviewer_command", item="x", target=""))
    log.append(
        "ask.answered",
        filed2.id,
        {"id": filed2.id, "outcome": "approved", "digest": filed2.digest, "by": "me",
         "human": True, "source": "terminal", "level": 4, "via": "signed"},
    )  # fmt: skip
    monkeypatch.setitem(E.LEVEL_VERIFIERS, "signed", lambda row, cfg: False)
    got = C.operator_gate(
        log, cfg, _canon(verb="config.reviewer_command", item="x", target=""),
        approval_id=filed2.id, now=T0 + 3,
    )  # fmt: skip
    assert not got.ok


def test_a_t3_verb_answered_at_the_terminal_is_never_enough(log):
    cfg = Config()
    t3 = _canon(verb="config.reviewer_command", item="", target="")
    filed = _request(log, cfg, t3)
    _approve(log, cfg, filed)
    got = C.operator_gate(log, cfg, t3, approval_id=filed.id, now=T0 + 1)
    assert not got.ok and "L4" in got.message


def test_an_id_that_is_not_a_command_approval_is_refused(log):
    cfg = Config()
    plain = E.request(log, cfg, subject="s", digest="d", now=T0)
    for bad in ("Qnope", plain.id):
        got = C.operator_gate(log, cfg, _canon(), approval_id=bad, now=T0)
        assert not got.ok and "no such approval request" in got.message


def test_one_approval_is_spent_by_exactly_one_of_many_racing_processes(log, repo):
    """The log lock is between PROCESSES (two `ddflow` runs), so the race is run as processes."""
    cfg = Config()
    filed = _approved(log, cfg)
    code = (
        "import sys, time;"
        "from pathlib import Path;"
        "from ddflow.infra.log import EventLog;"
        "from ddflow.services.approvals import consume as C;"
        "from ddflow.services.approvals.canon import Canon;"
        "c = Canon(verb='gate.skip.failed_review', agent='asker', item='fix-X', target='critic', state='s1');"
        "ok = C.spend(EventLog(Path(sys.argv[1]), 'asker'), c, sys.argv[2], now=float(sys.argv[3])).ok;"
        "print('SPENT' if ok else 'REFUSED')"
    )
    answered = clock.epoch(asks_of(_state(log))[filed.id]["answered_at"], default=0.0)
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(repo), filed.id, str(answered + 5)],
            stdout=subprocess.PIPE,
            text=True,
        )
        for _ in range(6)
    ]
    outs = [p.communicate()[0].strip() for p in procs]
    assert outs.count("SPENT") == 1 and outs.count("REFUSED") == 5, outs
    assert asks_of(_state(log))[filed.id]["used"] == 1


def test_a_row_without_a_recorded_level_still_counts_as_a_terminals_word():
    assert E.verified_level({"level": 0, "via": "cli"}) == 1
    assert E.verified_level({"level": -1, "via": ""}) == 1
    assert E.verified_level({}) == 1


def test_complete_approval_request_reason_names_one_refusal(log):
    """B-approval-refusal-doubled-prefix: `complete --approval request` once read
    'refusing: refusing: ...' -- `consume.operator_gate` already prefixes its refusal and
    `api/lifecycle/complete/approval._approved_force` prefixed it again. The refusal an
    operator acts on carries exactly one prefix, on BOTH refusal paths `_approved_force`
    hands back: a filed request (`--approval request`) and a refused spend (a real id)."""
    from ddflow.api.lifecycle.complete.approval import _approved_force

    cfg = Config()
    # "request" files a request (`operator_gate` -> `R.file` -> "refusing: " + route);
    # "Qnope" is a real id the spend refuses (`spend` -> "refusing: {why}."). The old caller
    # prepended "refusing: " to either, stacking the prefix.
    for approval in ("request", "Qnope"):
        ok, _stamp, why = _approved_force(
            log, cfg, "fix-X", ["a required gate is not passed"], approval, "bot"
        )
        assert not ok, approval
        assert why.startswith("refusing: "), (approval, why)
        assert not why.startswith("refusing: refusing: "), (approval, why)

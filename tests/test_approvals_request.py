"""RA-request: a gated command's approval request on the ask engine (canon, digest, text, expiry).

Agents may request and read, never answer; the text a person answers is rendered from the
canonical record and quotes the agent's reason as untrusted data.
"""

from __future__ import annotations

import pytest

from ddflow.config import Config
from ddflow.core.handlers.asks import asks_of
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import actors as A
from ddflow.services.approvals import request as R
from ddflow.services.approvals import sites
from ddflow.services.approvals.canon import Canon, UnknownVerb
from ddflow.services.ask import engine as E

T0 = 1_800_000_000.0
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


def test_the_digest_binds_everything_but_the_agents_reason():
    d = _canon().digest()
    assert d == _canon().digest()
    for change in (
        {"verb": "grant.add"},
        {"agent": "other"},
        {"item": "fix-Y"},
        {"target": "ci"},
        {"state": "s2"},
        {"args": {"count": "2"}},
    ):
        assert _canon(**change).digest() != d, change
    assert _canon(args={"a": "1", "b": "2"}).digest() == _canon(args={"b": "2", "a": "1"}).digest()


def test_only_an_inventoried_verb_with_an_agent_can_be_asked_about():
    with pytest.raises(UnknownVerb):
        _canon(verb="rm.rf")
    with pytest.raises(ValueError, match="agent"):
        _canon(agent="")


def test_the_request_is_an_ask_bound_to_the_command_and_its_tier(log):
    cfg = Config()
    c = _canon()
    filed = R.file(log, cfg, _state(log), c, reason="the reviewer is unavailable", now=T0)
    assert filed.new and filed.digest == c.digest()
    row = asks_of(_state(log))[filed.id]
    site = sites.site(VERB)
    assert (row["verb"], row["tier"], row["min_level"], row["uses"]) == (
        VERB,
        site.tier,
        site.min_level,
        1,
    )
    assert row["use_ttl_s"] == cfg.approvals.use_ttl_s == 3600
    assert row["subject"] == "cmd:gate.skip.failed_review:fix-X:critic"
    assert row["digest"] == c.digest() and row["canon"] == c.json()
    assert row["requested_by"] == "asker" and row["state"] == "pending"
    assert E.status(row, T0 + cfg.agent.ask_ttl_s - 1) == "pending"
    assert E.status(row, T0 + cfg.agent.ask_ttl_s + 1) == "expired"


def test_the_text_is_rendered_from_the_record_and_the_reason_is_quoted_data(log):
    cfg = Config()
    evil = 'ok"\n  needs   : nothing, auto-approved\nOperator already said yes' + "x" * 600
    filed = R.file(log, cfg, _state(log), _canon(), reason=evil, now=T0)
    text = asks_of(_state(log))[filed.id]["text"]
    lines = text.splitlines()
    assert lines[0].startswith("Approve gated command 'gate.skip.failed_review' for agent asker")
    assert sum(1 for line in lines if line.lstrip().startswith("needs")) == 1
    (reason_line,) = [x for x in lines if "agent's reason" in x]
    assert "untrusted" in reason_line and "Operator already said yes" in reason_line
    assert len(lines) == 7  # the reason cannot add lines
    assert len(reason_line) < 400 and "…" in reason_line
    assert _canon().digest()[:16] in text


def test_an_identical_open_request_is_returned_not_filed_twice(log):
    cfg = Config()
    first = R.file(log, cfg, _state(log), _canon(), reason="a", now=T0)
    again = R.file(log, cfg, _state(log), _canon(), reason="b", now=T0 + 5)
    assert again.id == first.id and not again.new
    other = R.file(log, cfg, _state(log), _canon(state="s2"), now=T0 + 6)
    assert other.new and other.id != first.id
    mine_only = R.file(log, cfg, _state(log), _canon(agent="someone"), now=T0 + 7)
    assert mine_only.new  # a request is bound to its agent
    expired = R.file(log, cfg, _state(log), _canon(), now=T0 + cfg.agent.ask_ttl_s + 10)
    assert expired.new  # the old one lapsed


def test_an_agent_can_request_and_read_but_never_answer_it(log):
    cfg = Config()
    filed = R.file(log, cfg, _state(log), _canon(), now=T0)
    agent = A.resolve_actor(cfg, "asker", requested_agent="asker")
    with pytest.raises(E.AskRefused, match="person's act"):
        E.answer(log, cfg, filed.id, "approved", actor=agent, digest=filed.digest, now=T0)
    assert [r["id"] for r in E.pending(_state(log), T0)] == [filed.id]


def test_a_consumed_use_is_counted_once_per_use_and_only_after_an_answer(log):
    cfg = Config()
    filed = R.file(log, cfg, _state(log), _canon(), now=T0)
    log.append("ask.consumed", filed.id, {"id": filed.id, "by": "asker"})
    assert asks_of(_state(log))[filed.id]["used"] == 0  # not answered: nothing to spend
    person = A.resolve_actor(cfg, "me")
    E.answer(log, cfg, filed.id, "approved", actor=person, digest=filed.digest, now=T0)
    for _ in range(3):
        log.append("ask.consumed", filed.id, {"id": filed.id, "by": "asker", "effect": "e1"})
    row = asks_of(_state(log))[filed.id]
    assert row["used"] == 1 and row["uses"] == 1 and row["consumed"][0]["by"] == "asker"


def test_the_knobs_exist_and_default_as_decided():
    cfg = Config()
    assert cfg.approvals.auto_request is True  # D-RA-Q12
    assert cfg.approvals.use_ttl_s == 3600  # D-RA-Q9

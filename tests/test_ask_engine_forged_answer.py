"""B-ask-verdict-trusts-hand-appended-answer: `verdict` must not approve on an unproven answer.

An `ask.answered` line appended straight to the log (not through `engine.answer`) used to
yield an approved verdict even for an ask that needs a one-time code. The answer now records
its source and a proof of the code it presented; `verdict` re-checks both.
"""

from __future__ import annotations

import pytest

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import actors as A
from ddflow.services.ask import engine as E

T0 = 1_800_000_000.0
SUBJECT = "send:draft-1"


@pytest.fixture(autouse=True)
def _a_person(monkeypatch):
    for var in ("DDFLOW_AGENT", "CLAUDECODE"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def log(repo):
    return EventLog(repo, "asker")


def _state(log):
    return fold(log.read_all(), strict=False)


def _verdict(log):
    return E.verdict(_state(log), SUBJECT, E.digest_of("hello"), T0)


def _ask(log, cfg, **kw):
    return E.request(log, cfg, subject=SUBJECT, content="hello", now=T0, **kw)


def _forge(log, ask_id, **over):
    data = {
        "id": ask_id,
        "outcome": "approved",
        "digest": E.digest_of("hello"),
        "by": "me",
        "human": True,
        "coded": True,
        "via": "cli",
        "role": "operator",
    }
    data.update(over)
    log.append("ask.answered", ask_id, data)


def test_a_hand_appended_answer_does_not_approve_a_coded_ask(log):
    cfg = Config()
    ask = _ask(log, cfg, with_code=True)
    _forge(log, ask.id)
    assert not _verdict(log).ok


def test_a_hand_appended_answer_with_a_wrong_code_proof_does_not_approve(log):
    cfg = Config()
    ask = _ask(log, cfg, with_code=True)
    _forge(log, ask.id, source="credential", code_proof="0" * 64)
    assert not _verdict(log).ok


def test_an_answer_without_a_recorded_source_does_not_approve(log):
    cfg = Config()
    ask = _ask(log, cfg)
    _forge(log, ask.id, coded=False)
    assert not _verdict(log).ok


def test_the_real_paths_still_approve(log):
    cfg = Config()
    d = E.digest_of("hello")
    person = A.resolve_actor(cfg, "me")
    plain = _ask(log, cfg)
    E.answer(log, cfg, plain.id, "approved", actor=person, digest=d, now=T0)
    assert _verdict(log).ok

    coded = E.request(log, cfg, subject="s2", content="hello", now=T0, with_code=True)
    ch = A.resolve_actor(cfg, "gw", credential_role="operator", authenticated=True)
    E.answer(log, cfg, coded.id, "approved", actor=ch, digest=d, code=coded.code, now=T0)
    assert E.verdict(_state(log), "s2", d, T0).ok

"""B1018a598b1: revoking a DENIED ask must be refused (it flipped denied to withdrawn)."""

from __future__ import annotations

import pytest

from ddflow.config import Config
from ddflow.core.handlers.asks import asks_of
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import actors as A
from ddflow.services.ask import engine as E


@pytest.fixture(autouse=True)
def _a_person(monkeypatch):
    for var in ("DDFLOW_AGENT", "CLAUDECODE"):
        monkeypatch.delenv(var, raising=False)


def test_a_denied_ask_cannot_be_revoked_but_an_approved_one_can(repo):
    log, cfg = EventLog(repo, "bot"), Config()
    person = A.resolve_actor(cfg, "me")
    denied = E.request(log, cfg, subject="a", digest="d")
    E.answer(log, cfg, denied.id, "denied", actor=person, digest="d")
    with pytest.raises(E.AskRefused, match="nothing to revoke"):
        E.revoke(log, denied.id, actor=person)
    assert asks_of(fold(log.read_all(), strict=False))[denied.id]["state"] == "answered"
    ok = E.request(log, cfg, subject="b", digest="d")
    E.answer(log, cfg, ok.id, "approved", actor=person, digest="d")
    E.revoke(log, ok.id, actor=person)
    assert asks_of(fold(log.read_all(), strict=False))[ok.id]["state"] == "withdrawn"

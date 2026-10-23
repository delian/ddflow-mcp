"""RA-audit: the approvals timeline and what looks wrong (D-RA-5)."""

from __future__ import annotations

import json
import sys
import time
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
from ddflow.services.approvals import request as R
from ddflow.services.approvals.canon import Canon
from ddflow.services.ask import engine as E

VERB = "gate.skip.failed_review"


@pytest.fixture(autouse=True)
def _a_person(monkeypatch):
    for var in ("DDFLOW_AGENT", *AP.HARNESS_MARKERS):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def log(repo):
    return EventLog(repo, "bot")


def _state(log):
    return fold(log.read_all(), strict=False)


def _canon(**over):
    base = {"verb": VERB, "agent": "bot", "item": "T1", "target": "critic", "state": "s1"}
    base.update(over)
    return Canon(**base)


def _file(log, cfg, canon=None):
    filed = R.file(log, cfg, _state(log), canon or _canon(), reason="r")
    return filed


def _approve(log, cfg, filed):
    E.answer(log, cfg, filed.id, "approved", actor=A.resolve_actor(cfg, "me"), digest=filed.digest)


def test_the_timeline_shows_who_asked_who_answered_and_what_was_spent(log):
    cfg = Config()
    a = _file(log, cfg)
    b = _file(log, cfg, _canon(item="T2", verb="grant.add"))
    _approve(log, cfg, a)
    assert C.operator_gate(log, cfg, _canon(), approval_id=a.id).ok
    rows = AU.rows(_state(log), cfg)
    by_id = {r["id"]: r for r in rows}
    assert by_id[a.id]["approver"] == "me" and by_id[a.id]["channel"] == "cli"
    assert by_id[a.id]["level"] == 1 and (by_id[a.id]["used"], by_id[a.id]["uses"]) == (1, 1)
    assert by_id[a.id]["spent_by"] == ["bot"] and by_id[b.id]["status"] == "pending"
    assert [r["id"] for r in AU.rows(_state(log), cfg, verb="grant.add")] == [b.id]
    assert [r["id"] for r in AU.rows(_state(log), cfg, agent="nobody")] == []
    assert AU.rows(_state(log), cfg, since=time.time() + 3600) == []
    assert a.id in AU.line(by_id[a.id])


def test_a_hand_written_approval_is_flagged_and_cannot_be_spent(log):
    cfg = Config()
    filed = _file(log, cfg)
    log.append(
        "ask.answered",
        filed.id,
        {"id": filed.id, "outcome": "approved", "digest": filed.digest, "by": "me", "human": True},
    )
    found = AU.findings(_state(log), cfg)
    assert any(filed.id in f and "without proof" in f for f in found), found
    spent = C.operator_gate(log, cfg, _canon(), approval_id=filed.id)
    assert not spent.ok and "source" in spent.message  # flagged AND unspendable


def test_an_approval_left_unspent_is_reported_when_its_window_runs_out(log):
    cfg = Config()
    filed = _file(log, cfg)
    _approve(log, cfg, filed)
    now = clock.epoch(asks_of(_state(log))[filed.id]["answered_at"], default=0.0)
    assert not [f for f in AU.findings(_state(log), cfg, now=now + 5) if filed.id in f]
    soon = AU.findings(_state(log), cfg, now=now + cfg.approvals.use_ttl_s - 60)
    assert any("within minutes" in f for f in soon)
    late = AU.findings(_state(log), cfg, now=now + cfg.approvals.use_ttl_s + 5)
    assert any("expired unused" in f for f in late)
    C.operator_gate(log, cfg, _canon(), approval_id=filed.id, now=now + 5)
    assert not [f for f in AU.findings(_state(log), cfg, now=now + 10**6) if filed.id in f]


def test_the_standing_limits_are_said_once_each(log):
    cfg = Config()
    notes = AU.standing_notes(cfg, _state(log))
    assert any("transcript" in n for n in notes) and any(
        "no approval signer pinned" in n for n in notes
    )
    cfg.approvals.accept_harness = False
    assert not any("transcript" in n for n in AU.standing_notes(cfg, _state(log)))


def test_the_log_command(repo):
    run_cli(repo, "init")
    log = EventLog(repo, "bot")
    filed = _file(log, Config())
    code, out, err = run_cli(repo, "--json", "approvals", "log")
    assert code == 0, err
    body = json.loads(out)
    assert [r["id"] for r in body["approvals"]] == [filed.id] and body["warnings"]
    code, out, _e = run_cli(repo, "approvals", "log", "--verb", "grant.add")
    assert code == 0 and "no approval requests" in out
    assert run_cli(repo, "approvals", "log", "--since", "not-a-date")[0] == 1

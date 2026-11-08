"""`wait --approval`: a lane blocked on its own operator approval wakes when it is answered.

Before this, a lane that filed a person-only approval (complete.force, claim.force,
accept_baseline_red, gate.skip) had to END and wait for the controller to re-dispatch a spend
on the operator's answer; the answer woke nobody. `ddflow wait --approval <id>` keeps the lane
alive (its leases renewed) until the approval is answered, then returns so the lane runs its own
gated command with `--approval <id>` and spends it. Waiting never spends the approval itself.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from ddflow.api import lifecycle as A
from ddflow.config import Config
from ddflow.core import outcome as O
from ddflow.core.handlers.asks import asks_of
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import actors
from ddflow.services.approvals import consume as C
from ddflow.services.approvals import request as R
from ddflow.services.approvals.canon import Canon
from ddflow.services.ask import engine as E

VERB = "gate.skip.failed_review"


@pytest.fixture(autouse=True)
def _a_person(monkeypatch):
    for var in ("DDFLOW_AGENT", "CLAUDECODE"):
        monkeypatch.delenv(var, raising=False)


def _log(repo: Path) -> EventLog:
    return EventLog(repo, "asker")


def _state(log: EventLog):
    return fold(log.read_all(), strict=False)


def _canon(**over) -> Canon:
    base = {"verb": VERB, "agent": "asker", "item": "fix-X", "target": "critic", "state": "s1"}
    base.update(over)
    return Canon(**base)


def _request(log: EventLog, cfg: Config, canon: Canon | None = None):
    return R.file(log, cfg, _state(log), canon or _canon(), reason="r")


def _approve(log: EventLog, cfg: Config, filed, outcome="approved", now=None) -> None:
    person = actors.resolve_actor(cfg, "me")
    E.answer(log, cfg, filed.id, outcome, actor=person, digest=filed.digest, level=1, now=now)


def test_a_lane_waits_while_pending_then_wakes_ready_and_spends_the_approval(repo):
    cfg = Config()
    log = _log(repo)
    filed = _request(log, cfg)  # still pending: the operator has not answered yet

    # While it is unanswered the wait refuses to guess, and names the wait that would help.
    still = A.wait(repo, approval=filed.id, timeout_s=0, agent="asker")
    assert still.exit == O.NOTHING, still.reason
    assert not still.data["woke"] and still.data["ready"] == []

    # The operator answers while the lane waits: the wait wakes READY, and does not spend.
    t = threading.Thread(target=lambda: _approve(log, cfg, filed), daemon=True)
    t.start()
    started = time.monotonic()
    out = A.wait(repo, approval=filed.id, timeout_s=20, poll_s=0.05, agent="asker")
    t.join(5)
    assert out.exit == O.OK, out.reason
    assert out.data["woke"] and out.data["ready"] == [filed.id]
    assert out.data["approval"] == filed.id
    assert time.monotonic() - started < 10, "woke by timeout, not by the answer"

    # Waiting never spends the approval: the lane's own gated command does.
    assert asks_of(_state(log))[filed.id]["used"] == 0
    got = C.operator_gate(log, cfg, _canon(), approval_id=filed.id)
    assert got.ok and got.ask_id == filed.id
    assert asks_of(_state(log))[filed.id]["used"] == 1


def test_an_answered_denial_is_hopeless_not_waited_for(repo):
    cfg = Config()
    log = _log(repo)
    filed = _request(log, cfg)
    _approve(log, cfg, filed, outcome="denied")
    out = A.wait(repo, approval=filed.id, timeout_s=5, poll_s=0.05, agent="asker")
    assert out.exit == O.NOTHING and "denied" in out.reason


def test_a_spent_approval_is_hopeless_not_ready(repo):
    cfg = Config()
    log = _log(repo)
    filed = _request(log, cfg)
    _approve(log, cfg, filed)
    assert C.operator_gate(log, cfg, _canon(), approval_id=filed.id).ok  # spent once
    out = A.wait(repo, approval=filed.id, timeout_s=5, poll_s=0.05, agent="asker")
    assert out.exit == O.NOTHING and "spent" in out.reason


def test_an_answered_question_is_hopeless_not_an_approval(repo):
    cfg = Config()
    log = _log(repo)
    asked = E.request(log, cfg, subject="q", kind=E.QUESTION, text="?", ttl_s=3600)
    E.answer(
        log,
        cfg,
        asked.id,
        E.ANSWERED_WORD,
        actor=actors.resolve_actor(cfg, "me"),
        text="yes",
    )
    out = A.wait(repo, approval=asked.id, timeout_s=5, poll_s=0.05, agent="asker")
    assert out.exit == O.NOTHING and "not an approval" in out.reason


def test_a_use_ttl_lapsed_approval_is_hopeless_not_ready(repo):
    # An answer older than its use window (use_ttl_s) re-running the gated command cannot
    # spend: wait must not wake READY for it (consume._problem refuses the same row).
    cfg = Config()
    cfg.approvals.use_ttl_s = 1
    log = _log(repo)
    filed = _request(log, cfg)
    _approve(log, cfg, filed)
    time.sleep(2)  # now past answered_at + use_ttl_s (1s)
    out = A.wait(repo, approval=filed.id, timeout_s=5, poll_s=0.05, agent="asker")
    assert out.exit == O.NOTHING and "more than 1s ago" in out.reason


def test_an_unknown_approval_is_hopeless(repo):
    out = A.wait(repo, approval="Qdeadbeefdeadbeef", timeout_s=5, agent="asker")
    assert out.exit == O.NOTHING and "no such approval" in out.reason

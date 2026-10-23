"""The acceptance scenario: an agent skips a FAILED review gate with the operator's approval.

`ddflow gate skip` over a failed review gate is the operator's override. An agent is refused,
and the refusal files an expiring request (auto_request) and names its id; a PERSON answers it
(`ddflow approvals approve`, ssh works); the agent re-runs with `--approval ID` and the skip is
recorded with who approved, by which channel and level, and the digest.
"""

from __future__ import annotations

import json
import sys
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
from ddflow.services.ask import engine as E

SKIP = ("gate", "skip", "T1", "critic", "--reason", "the reviewer is down")


@pytest.fixture(autouse=True)
def _a_person(monkeypatch):
    for var in ("DDFLOW_AGENT", *AP.HARNESS_MARKERS):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def proj(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "t.py")
    run_cli(repo, "task", "add", "T2", "--globs", "u.py")
    for item in ("T1", "T2"):
        code, _o, err = run_cli(
            repo, "gate", "record", item, "critic", "--outcome", "failed", "--reason", "findings"
        )
        assert code == 0, err
    return repo


def _state(repo):
    return fold(EventLog(repo, "probe").read_all(), strict=False)


def _approve(repo, ask_id):
    shown = _state(repo)
    digest = asks_of(shown)[ask_id]["digest"]
    code, _o, err = run_cli(repo, "approvals", "approve", ask_id, "--digest", digest[:12])
    assert code == 0, err


def _refusal_id(err: str) -> str:
    word = err.split("approval ", 1)[1].split()[0]
    assert word.startswith("Q"), err
    return word


def _skipped(repo, item="T1") -> dict | None:
    it = _state(repo).items[item]
    runs = [r for r in it.runs_of("critic") if r.outcome == "skipped"]
    return runs[-1].evidence if runs else None


def test_an_agent_is_refused_and_the_refusal_files_the_request(proj):
    code, _o, err = run_cli(proj, *SKIP, agent="bot")
    assert code == 3 and "operator's" in err and "--approval" in err, err
    ask_id = _refusal_id(err)
    row = asks_of(_state(proj))[ask_id]
    assert row["verb"] == "gate.skip.failed_review" and row["requested_by"] == "bot"
    assert row["state"] == "pending" and "critic" in row["text"] and "T1" in row["text"]
    assert _skipped(proj) is None
    again = run_cli(proj, *SKIP, agent="bot")[2]
    assert _refusal_id(again) == ask_id and len(asks_of(_state(proj))) == 1  # the same request


def test_the_whole_scenario_with_an_approval(proj):
    ask_id = _refusal_id(run_cli(proj, *SKIP, agent="bot")[2])
    _approve(proj, ask_id)
    code, out, err = run_cli(proj, *SKIP, "--approval", ask_id, agent="bot")
    assert code == 0, (out, err)
    stamp = _skipped(proj)["approval"]
    assert stamp["ask"] == ask_id and stamp["proxy"] == "bot" and stamp["level"] == 1
    assert stamp["approved_by"] and stamp["via"] == "cli"
    replay = run_cli(proj, *SKIP, "--approval", ask_id, agent="bot")
    assert replay[0] == 3 and "spent" in replay[2]


def test_the_approval_does_not_stretch_to_another_gate_item_outcome_or_state(proj):
    ask_id = _refusal_id(run_cli(proj, *SKIP, agent="bot")[2])
    _approve(proj, ask_id)
    other_item = run_cli(proj, "gate", "skip", "T2", "critic", "--reason", "r",
                         "--approval", ask_id, agent="bot")  # fmt: skip
    assert other_item[0] == 3 and "other state" in other_item[2]
    hand_pass = run_cli(proj, "gate", "record", "T1", "critic", "--outcome", "passed",
                        "--reason", "r", "--approval", ask_id, agent="bot")  # fmt: skip
    assert hand_pass[0] == 3 and "other state" in hand_pass[2]
    assert _skipped(proj) is None and _skipped(proj, "T2") is None


def test_a_changed_gate_state_needs_a_new_approval(proj):
    ask_id = _refusal_id(run_cli(proj, *SKIP, agent="bot")[2])
    _approve(proj, ask_id)
    log = EventLog(proj, "someone")  # a new failed round: the state the person approved is gone
    log.append(
        "gate.failed", "T1", {"gate": "critic", "by": "m", "reason": "again", "evidence": {}}
    )
    code, _o, err = run_cli(proj, *SKIP, "--approval", ask_id, agent="bot")
    assert code == 3 and "other state" in err


def test_another_agent_and_an_unapproved_id_do_not_get_through(proj):
    ask_id = _refusal_id(run_cli(proj, *SKIP, agent="bot")[2])
    pending = run_cli(proj, *SKIP, "--approval", ask_id, agent="bot")
    assert pending[0] == 3 and "not answered" in pending[2]
    _approve(proj, ask_id)
    thief = run_cli(proj, *SKIP, "--approval", ask_id, agent="thief")
    assert thief[0] == 3 and _skipped(proj) is None


def test_an_agent_cannot_approve_its_own_request_to_get_through(proj):
    ask_id = _refusal_id(run_cli(proj, *SKIP, agent="bot")[2])
    digest = asks_of(_state(proj))[ask_id]["digest"]
    code, _o, err = run_cli(proj, "approvals", "approve", ask_id, "--digest", digest[:12],
                            agent="bot")  # fmt: skip
    assert code == 3
    log = EventLog(proj, "bot")  # and a hand-written answer approves nothing
    log.append("ask.answered", ask_id, {"id": ask_id, "outcome": "approved", "digest": digest,
                                        "by": "me", "human": True})  # fmt: skip
    code, _o, err = run_cli(proj, *SKIP, "--approval", ask_id, agent="bot")
    assert code == 3 and "source" in err
    assert _skipped(proj) is None


def test_a_person_at_a_terminal_is_unchanged(proj):
    code, out, err = run_cli(proj, *SKIP)
    assert code == 0, (out, err)
    assert "approval" not in (_skipped(proj) or {})


def test_the_request_can_also_be_filed_by_hand(proj):
    code, out, err = run_cli(proj, "--json", "approvals", "request", "gate.skip.failed_review",
                             "--item", "T1", "--target", "critic", agent="bot")  # fmt: skip
    assert code == 0, err
    ask_id = json.loads(out)["id"]
    _approve(proj, ask_id)
    code, _o, err = run_cli(proj, "gate", "skip", "T1", "critic", "--reason", "r",
                            "--approval", ask_id, agent="bot")  # fmt: skip
    assert (
        code == 3 and "other state" in err
    )  # by-hand requests carry no outcome arg: skip needs its own
    assert Config().approvals.auto_request is True
    assert A.resolve_actor(Config(), "me").source == "terminal" and E.APPROVED == "approved"

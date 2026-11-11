"""RA-e2e: the whole approval transcript, end to end, through the CLI and the engine.

refuse -> request -> person approves (terminal L1, typed chat L2) -> the agent spends it ->
replay, other command, expiry and revocation are refused. Every step runs in a throwaway
project; nothing leaves the machine. The attacks that need a hostile writer are in
tests/test_approvals_adversarial.py."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.config import Config
from ddflow.core import clock
from ddflow.core.handlers.asks import asks_of
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import approval as AP
from ddflow.services.approvals import consume as C
from ddflow.services.approvals.canon import Canon

SKIP = ("gate", "skip", "T1", "critic", "--reason", "the reviewer is down")


@pytest.fixture(autouse=True)
def _a_person(monkeypatch):
    for var in ("DDFLOW_AGENT", *AP.HARNESS_MARKERS):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def proj(repo, tmp_path):
    run_cli(repo, "init")
    cfgfile = repo / ".ddflow" / "config.toml"
    projects = tmp_path / "projects"
    cfgfile.write_text(cfgfile.read_text() + f"\n[approvals]\ntranscript_dirs = ['{projects}']\n")
    for item in ("T1", "T2"):
        run_cli(repo, "task", "add", item, "--title", f"{item} fixture", "--globs", f"{item}.py")
        code, _o, err = run_cli(
            repo, "gate", "record", item, "critic", "--outcome", "failed", "--reason", "findings"
        )
        assert code == 0, err
    return repo


def _state(repo):
    return fold(EventLog(repo, "probe").read_all(), strict=False)


def _row(repo, ask_id):
    return asks_of(_state(repo))[ask_id]


def _skipped(repo, item="T1"):
    return [r for r in _state(repo).items[item].runs_of("critic") if r.outcome == "skipped"]


def _refuse(repo, *argv, agent="bot"):
    code, _o, err = run_cli(repo, *(argv or SKIP), agent=agent)
    assert code == 3, err
    found = re.search(r"approval (Q[0-9a-f]{10})", err)
    assert found, err
    return found.group(1), err


def _approve_at_the_terminal(repo, ask_id):
    digest = _row(repo, ask_id)["digest"]
    code, _o, err = run_cli(repo, "approvals", "approve", ask_id, "--digest", digest[:12])
    assert code == 0, err


def test_transcript_terminal_approval_is_spent_exactly_once(proj):
    ask_id, refusal = _refuse(proj)
    assert "--approval" in refusal and "ddflow approvals ask" in refusal  # the route is named
    assert _row(proj, ask_id)["state"] == "pending" and not _skipped(proj)
    # the agent reads its own request back (what it shows the operator) ...
    code, out, err = run_cli(proj, "--json", "approvals", "ask", ask_id, agent="bot")
    assert code == 0, err
    assert json.loads(out)["input"]["questions"][0]["question"] == _row(proj, ask_id)["text"]
    # ... and spending it before the person answered is refused, spending nothing.
    code, _o, err = run_cli(proj, *SKIP, "--approval", ask_id, agent="bot")
    assert code == 3 and "not answered" in err and not _skipped(proj)
    _approve_at_the_terminal(proj, ask_id)
    code, out, err = run_cli(proj, *SKIP, "--approval", ask_id, agent="bot")
    assert code == 0, (out, err)
    run = _skipped(proj)[-1]
    stamp = run.evidence["approval"]
    assert stamp["ask"] == ask_id and stamp["level"] == 1 and stamp["via"] == "cli"
    assert _row(proj, ask_id)["used"] == 1
    # replay: the gate fails again, the very same command and id are refused, nothing is skipped
    code, _o, err = run_cli(
        proj, "gate", "record", "T1", "critic", "--outcome", "failed", "--reason", "again"
    )
    assert code == 0, err
    before = len(_skipped(proj))
    code, _o, err = run_cli(proj, *SKIP, "--approval", ask_id, agent="bot")
    assert code == 3 and len(_skipped(proj)) == before
    assert _row(proj, ask_id)["used"] == 1


def test_transcript_typed_chat_answer_is_level_two_and_spendable(proj, tmp_path):
    ask_id, _ = _refuse(proj)
    row = _row(proj, ask_id)
    d = tmp_path / "projects" / "slug"
    d.mkdir(parents=True)
    typed = {
        "type": "user",
        "uuid": "U1",
        "timestamp": "2026-10-24T10:00:00Z",
        "origin": {"kind": "human"},
        "message": {"content": f"approve {ask_id} {row['digest'][:8]}"},
    }
    (d / "s.jsonl").write_text(json.dumps(typed) + "\n")
    code, out, err = run_cli(proj, "approvals", "attest", ask_id, agent="bot")
    assert code == 0, (out, err)
    code, out, err = run_cli(proj, *SKIP, "--approval", ask_id, agent="bot")
    assert code == 0, (out, err)
    assert _skipped(proj)[-1].evidence["approval"]["level"] == 2
    assert _row(proj, ask_id)["used"] == 1


def test_transcript_an_approval_names_one_command_not_a_family(proj):
    ask_id, _ = _refuse(proj)
    _approve_at_the_terminal(proj, ask_id)
    # another item, another gate, another reason-free verb: each is refused and spends nothing
    code, _o, _err = run_cli(
        proj, "gate", "skip", "T2", "critic", "--reason", "r", "--approval", ask_id, agent="bot"
    )
    assert code == 3 and not _skipped(proj, "T2")
    assert _row(proj, ask_id)["used"] == 0
    code, _o, _err = run_cli(proj, "complete", "T1", "--approval", ask_id, agent="bot")
    assert code != 0 and _row(proj, ask_id)["used"] == 0
    assert run_cli(proj, *SKIP, "--approval", ask_id, agent="bot")[0] == 0  # the real one works


def test_transcript_a_revoked_approval_cannot_be_spent(proj):
    ask_id, _ = _refuse(proj)
    _approve_at_the_terminal(proj, ask_id)
    code, _o, err = run_cli(proj, "approvals", "revoke", ask_id)
    assert code == 0, err
    code, _o, err = run_cli(proj, *SKIP, "--approval", ask_id, agent="bot")
    assert code == 3 and not _skipped(proj)


def test_transcript_a_request_and_an_approval_expire(proj):
    cfg = Config()
    ask_id, _ = _refuse(proj)
    log = EventLog(proj, "bot")
    it = _state(proj).items["T1"]
    from ddflow.services.approvals import gate_skip as GS

    canon = GS.canon_for(it, "critic", "T1", "failed with no triaged findings on record",
                         "bot", "skipped")  # fmt: skip
    # unanswered past its request window: not spendable and not answerable
    late = clock.epoch(_row(proj, ask_id)["requested_at"], default=0.0) + cfg.agent.ask_ttl_s + 1
    got = C.operator_gate(log, cfg, canon, approval_id=ask_id, now=late)
    assert not got.ok
    # answered, then left past the use window
    ask2, _ = _refuse(proj, "gate", "skip", "T2", "critic", "--reason", "r")
    _approve_at_the_terminal(proj, ask2)
    answered = clock.epoch(_row(proj, ask2)["answered_at"], default=0.0)
    t2 = GS.canon_for(_state(proj).items["T2"], "critic", "T2", "failed with no triaged findings on record",
                      "bot", "skipped")  # fmt: skip
    stale = C.operator_gate(
        log, cfg, t2, approval_id=ask2, now=answered + cfg.approvals.use_ttl_s + 1
    )
    assert not stale.ok and "request a new approval" in stale.message


def test_transcript_a_standing_rule_is_capped_and_recorded_as_policy(proj):
    cfg = Config()
    cfg.approvals.policy = ["skip_after_unavailable"]
    log = EventLog(proj, "bot")
    from ddflow.services.approvals import gate_skip as GS

    for _ in range(3):
        run_cli(
            proj, "gate", "record", "T1", "critic", "--outcome", "unavailable", "--reason", "down"
        )
    run_cli(proj, "gate", "record", "T1", "critic", "--outcome", "failed", "--reason", "f")
    canon = GS.canon_for(_state(proj).items["T1"], "critic", "T1",
                         "failed with no triaged findings on record", "bot", "skipped")  # fmt: skip
    first = C.operator_gate(log, cfg, canon)
    assert first.ok and first.stamp["via"] == "policy"
    row = _row(proj, first.ask_id)
    assert row["human"] is False and row["answered_by"].startswith("policy:")
    second = C.operator_gate(log, cfg, canon)  # the cap: one use per item and target
    assert not second.ok and second.ask_id  # a person is asked instead
    assert _row(proj, second.ask_id)["state"] == "pending"
    assert isinstance(canon, Canon)

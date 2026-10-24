"""RA-t3-chat (D-RA-T3-chat): a tier-3 act approved by a TYPED chat answer, only under the
operator's opt-in on the host, never by an AskUserQuestion answer, never for the opt-in itself,
rated L2-typed and warned on at every use."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ddflow.config import Config
from ddflow.core.handlers.asks import asks_of
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import approval as AP
from ddflow.services import configwrite as CW
from ddflow.services.approvals import audit as AU
from ddflow.services.approvals import consume as C
from ddflow.services.approvals import harness as H
from ddflow.services.approvals import request as R
from ddflow.services.approvals.canon import Canon

VERB = "reviewers.approve_command"


@pytest.fixture(autouse=True)
def _a_person(monkeypatch):
    for var in ("DDFLOW_AGENT", *AP.HARNESS_MARKERS):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def log(repo):
    return EventLog(repo, "bot")


def _cfg(tmp_path, *, on=True, source="local", forbid=False):
    c = Config()
    c.approvals.transcript_dirs = [str(tmp_path / "projects")]
    c.approvals.accept_chat_for_t3 = on
    c.approvals.forbid_chat_for_t3 = forbid
    c.sources["approvals.accept_chat_for_t3"] = source
    return c


@pytest.fixture
def cfg(tmp_path):
    return _cfg(tmp_path)


def _state(log):
    return fold(log.read_all(), strict=False)


def _canon(**over):
    base = {"verb": VERB, "agent": "bot", "item": "cmdrev", "state": "dig1"}
    base.update(over)
    return Canon(**base)


def _file(log, cfg):
    filed = R.file(log, cfg, _state(log), _canon(), reason="r")
    return asks_of(_state(log))[filed.id]


def _write(cfg, rows):
    d = Path(cfg.approvals.transcript_dirs[0]) / "slug"
    d.mkdir(parents=True, exist_ok=True)
    (d / "s1.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


def _typed(row):
    return {"type": "user", "uuid": "U1", "timestamp": "2026-10-24T10:00:00Z",
            "origin": {"kind": "human"},
            "message": {"content": f"approve {row['id']} {row['digest'][:8]}"}}  # fmt: skip


def test_a_typed_answer_approves_a_t3_act_and_is_graded(log, cfg):
    row = _file(log, cfg)
    assert row["min_level"] == 4
    _write(cfg, [_typed(row)])
    ok, message = H.attest(log, cfg, row["id"])
    assert ok, message
    got = C.operator_gate(log, cfg, _canon(), approval_id=row["id"])
    assert got.ok and got.stamp["grade"] == "L2-typed"
    consumed = asks_of(_state(log))[row["id"]]["consumed"]
    assert consumed[0]["grade"] == "L2-typed"
    warn = AU.findings(_state(log), cfg)
    assert any("TYPED chat" in w and row["id"] in w for w in warn)
    notes = AU.standing_notes(cfg, _state(log))
    assert any("solo operator" in w for w in notes)
    assert not any("stay terminal-only" in n and "command reviewers" in n for n in notes)


def test_without_the_opt_in_nothing_is_accepted(log, tmp_path):
    cfg = _cfg(tmp_path, on=False)
    row = _file(log, cfg)
    _write(cfg, [_typed(row)])
    ok, message = H.attest(log, cfg, row["id"])
    assert not ok and "accept_chat_for_t3" in message


@pytest.mark.parametrize("source", ["file", "env", "default"])
def test_only_the_machine_local_layer_counts(log, tmp_path, source):
    cfg = _cfg(tmp_path, source=source)
    row = _file(log, cfg)
    _write(cfg, [_typed(row)])
    assert not H.attest(log, cfg, row["id"])[0]


def test_the_company_layer_forbids_whatever_the_opt_in_says(log, tmp_path):
    cfg = _cfg(tmp_path, forbid=True)
    row = _file(log, cfg)
    _write(cfg, [_typed(row)])
    assert not H.attest(log, cfg, row["id"])[0]


def test_a_forbid_in_a_file_layer_cannot_be_undone_by_a_later_layer(log, tmp_path, repo):
    cfg = _cfg(tmp_path)
    cfg.root = repo
    committed = repo / ".ddflow" / "config.toml"
    committed.parent.mkdir(exist_ok=True)
    committed.write_text("[approvals]\nforbid_chat_for_t3 = true\n")
    assert not H.chat_t3(cfg)  # the merged value says false here; the file says true
    committed.write_text("[approvals]\nforbid_chat_for_t3 = false\n")
    assert H.chat_t3(cfg)


def test_the_environment_can_forbid(log, tmp_path, monkeypatch):
    monkeypatch.setenv("DDFLOW_APPROVALS_FORBID_CHAT_FOR_T3", "true")
    assert not H.chat_t3(_cfg(tmp_path))


def test_an_askuserquestion_answer_never_satisfies_a_t3_act(log, cfg):
    row = _file(log, cfg)
    yes, _ = H.labels(row["id"])
    _write(cfg, [
        {"type": "assistant", "uuid": "A1", "message": {"content": [
            {"type": "tool_use", "id": "U1", "name": H.TOOL,
             "input": {"questions": [{"question": row["text"]}]}}]}},
        {"type": "user", "uuid": "R1", "sourceToolAssistantUUID": "A1",
         "timestamp": "2026-10-24T10:00:00Z",
         "toolUseResult": {"answers": {row["text"]: yes}},
         "permissionDecision": {"decision": "accept", "source": "user_temporary"},
         "message": {"content": [{"type": "tool_result", "tool_use_id": "U1", "content": "x"}]}},
    ])  # fmt: skip
    assert H.find(cfg, row) is not None  # a valid answer to a T2 act: the T3 filter is what refuses
    ok, _message = H.attest(log, cfg, row["id"])
    assert not ok
    assert asks_of(_state(log))[row["id"]]["state"] == "pending"


def test_a_spend_after_the_opt_in_is_switched_off_is_refused(log, cfg, tmp_path):
    row = _file(log, cfg)
    _write(cfg, [_typed(row)])
    assert H.attest(log, cfg, row["id"])[0]
    off = _cfg(tmp_path, on=False)
    assert not C.operator_gate(log, off, _canon(), approval_id=row["id"]).ok


def test_the_typed_row_must_still_be_there_at_spend_time(log, cfg):
    row = _file(log, cfg)
    _write(cfg, [_typed(row)])
    assert H.attest(log, cfg, row["id"])[0]
    _write(cfg, [])
    assert not C.operator_gate(log, cfg, _canon(), approval_id=row["id"]).ok


def test_a_handwritten_answer_is_not_a_typed_one(log, cfg):
    row = _file(log, cfg)
    log.append("ask.answered", row["id"], {
        "id": row["id"], "outcome": "approved", "by": "x", "human": True, "digest": row["digest"],
        "via": "harness", "level": 2, "proof": "typed:forged",
    })  # fmt: skip
    assert not C.operator_gate(log, cfg, _canon(), approval_id=row["id"]).ok


def test_a_t2_verb_is_unchanged_by_the_opt_in(log, cfg):
    canon = _canon(verb="gate.skip.failed_review", item="T1", target="critic")
    filed = R.file(log, cfg, _state(log), canon, reason="r")
    assert asks_of(_state(log))[filed.id]["min_level"] == 1


def test_no_tool_turns_the_opt_in_on(repo):
    local = CW.apply_edit(repo, CW.SetPairs([("approvals.accept_chat_for_t3", CW.Spelled("true"))]), layer="local")
    committed = CW.apply_edit(repo, CW.SetPairs([("approvals.accept_chat_for_t3", CW.Spelled("true"))]))
    for res in (local, committed):
        assert res.error and "no tool writes it" in res.error
    block = CW.apply_edit(repo, CW.AppendText("[approvals]\naccept_chat_for_t3 = true\n"), layer="local")
    assert block.error and "cannot be enabled remotely" in block.error


def test_no_tool_lifts_the_forbid(repo):
    on = CW.apply_edit(repo, CW.SetPairs([("approvals.forbid_chat_for_t3", CW.Spelled("true"))]))
    assert not on.error
    off = CW.apply_edit(repo, CW.SetPairs([("approvals.forbid_chat_for_t3", CW.Spelled("false"))]))
    assert off.error and "only tightens" in off.error

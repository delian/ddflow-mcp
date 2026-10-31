"""RA-audit-wire-2: doctor notes from the approvals audit and `gate list --overrides`."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import approval as AP
from ddflow.services.approvals import overrides as OV

STAMP = {"ask": "Qabc", "approved_by": "me", "via": "harness", "level": 2, "digest": "d" * 16}


@pytest.fixture(autouse=True)
def _a_person(monkeypatch):
    for var in ("DDFLOW_AGENT", *AP.HARNESS_MARKERS):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def repo_with_overrides(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "t.py")
    log = EventLog(repo, "bot")
    log.append("gate.skipped", "T1", {"gate": "critic", "by": "bot", "reason": "down",
                                      "evidence": {"approval": STAMP}})  # fmt: skip
    log.append("gate.skipped", "T1", {"gate": "research", "by": "me", "reason": "n/a",
                                      "evidence": {}})  # fmt: skip
    log.append("item.completed", "T1", {"forced": True, "overridden": ["gate ci"], **STAMP})
    log.append("skew.overridden", "T1", {"reason": "older ddflow"})
    return repo


def _rows(repo):
    log = EventLog(repo, "probe")
    events = log.read_all()
    return OV.rows(events, fold(events, strict=False), Config())


def test_rows_name_every_override_with_approver_and_level(repo_with_overrides):
    by_kind = {r["kind"]: r for r in _rows(repo_with_overrides)}
    assert set(by_kind) == {OV.APPROVED_SKIP, OV.SKIP, OV.FORCED, OV.RELAYED}
    a = by_kind[OV.APPROVED_SKIP]
    assert (a["gate"], a["approver"], a["channel"], a["level"]) == ("critic", "me", "harness", 2)
    assert by_kind[OV.FORCED]["approver"] == "me" and by_kind[OV.FORCED]["level"] == 2
    assert by_kind[OV.SKIP]["approver"] == ""


def test_gate_list_overrides_prints_them(repo_with_overrides):
    code, out = run_cli(repo_with_overrides, "gate", "list", "--overrides")[:2]
    assert code == 0
    assert "approved gate skip T1.critic" in out and "approved by me via harness L2" in out
    assert "forced completion T1" in out
    code, out = run_cli(repo_with_overrides, "gate", "list", "--overrides", "--json")[:2]
    data = json.loads(out)
    assert data["overrides"] and data["count"] == len(data["overrides"])


def test_overrides_refuses_with_refuted(repo_with_overrides):
    code = run_cli(repo_with_overrides, "gate", "list", "--overrides", "--refuted")[0]
    assert code != 0


def test_doctor_notes_count_overrides_never_secrets(repo_with_overrides):
    code, out = run_cli(repo_with_overrides, "doctor", "--json")[:2]
    notes = "\n".join(json.loads(out)["notes"])
    assert "approvals audit: 1 forced completion(s)" in notes
    assert "approvals audit: 1 approved gate skip(s)" in notes
    assert "approvals audit: 1 relayed consent(s)" in notes
    assert "d" * 16 not in notes


def test_a_typed_chat_t3_use_and_a_claim_force_are_listed(repo):
    run_cli(repo, "init")
    log = EventLog(repo, "bot")
    for ask, verb, grade in (("Q1", "complete.force", "L2-typed"), ("Q2", "claim.force", "")):
        log.append("ask.requested", ask, {"id": ask, "verb": verb, "uses": 1, "subject": "T1"})
        log.append(
            "ask.answered", ask, {"id": ask, "outcome": "approved", "by": "me", "via": "harness"}
        )
        log.append("ask.consumed", ask, {"id": ask, "by": "bot", "verb": verb, "grade": grade})  # fmt: skip
    kinds = sorted(r["kind"] for r in _rows(repo))
    assert kinds == sorted([OV.TYPED, OV.CLAIM_FORCE])
    notes = "\n".join(json.loads(run_cli(repo, "doctor", "--json")[1])["notes"])
    assert "approvals audit: 1 typed-chat T3 use(s)" in notes
    assert "TYPED chat answer" in notes  # the per-use warning from the audit findings

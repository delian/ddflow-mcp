"""RA-audit-wire: the item report names who approved a skipped gate and how."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import approval as AP
from ddflow.services import report_gates as RG


@pytest.fixture(autouse=True)
def _a_person(monkeypatch):
    for var in ("DDFLOW_AGENT", *AP.HARNESS_MARKERS):
        monkeypatch.delenv(var, raising=False)


def _item(repo):
    return fold(EventLog(repo, "probe").read_all(), strict=False).items["T1"]


def test_a_skip_with_an_approval_names_the_approver_the_channel_and_the_ask(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "t.py")
    assert run_cli(repo, "gate", "skip", "T1", "research", "--reason", "n/a here")[0] == 0
    plain = RG.overrides(_item(repo), {}, [], [])
    assert plain == ["gate research skipped: n/a here"]  # a person's skip: no tail
    log = EventLog(repo, "bot")
    log.append(
        "gate.skipped",
        "T1",
        {
            "gate": "critic", "by": "bot", "reason": "reviewer down",
            "evidence": {"approval": {"ask": "Qabc", "approved_by": "me", "via": "harness", "level": 2}},
        },
    )  # fmt: skip
    lines = RG.overrides(_item(repo), {}, [], [])
    assert any("approved by me via harness at L2, approval Qabc" in line for line in lines), lines

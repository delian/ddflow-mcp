"""RA-cli: a request waiting for the operator shows in the brief and the status, and only then."""

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
from ddflow.services.approvals import request as R
from ddflow.services.approvals.canon import Canon


@pytest.fixture(autouse=True)
def _a_person(monkeypatch):
    for var in ("DDFLOW_AGENT", *AP.HARNESS_MARKERS):
        monkeypatch.delenv(var, raising=False)


def test_the_brief_and_status_say_nothing_until_a_request_waits(repo):
    run_cli(repo, "init")
    code, out, _e = run_cli(repo, "brief")
    assert code == 0 and "approval request" not in out
    _c, out, _e = run_cli(repo, "--json", "status")
    assert "approvals_pending" not in json.loads(out)

    log = EventLog(repo, "bot")
    st = fold(log.read_all(), strict=False)
    canon = Canon(verb="grant.add", agent="bot", item="T1", target="critic", state="s")
    filed = R.file(log, Config(), st, canon, reason="x")

    code, out, _e = run_cli(repo, "brief")
    assert "1 approval request(s) wait for the operator" in out and filed.id in out
    _c, out, _e = run_cli(repo, "--json", "status")
    assert [r["id"] for r in json.loads(out)["approvals_pending"]] == [filed.id]
    _c, out, _e = run_cli(repo, "status")
    assert "approval request(s) waiting for the operator" in out

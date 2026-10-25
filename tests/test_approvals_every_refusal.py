"""approvals-every-refusal: whichever class filed the request, its pending row carries the
digest and the answer form on every surface the operator reads remotely -- `ddflow approvals
pending` and the brief/status notice an MCP or chat surface carries -- and the config-edit
class (the one the wave answered by local config) has an approval-request route an agent can
drive to a recorded answer."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.config import Config
from ddflow.core.handlers.asks import asks_of
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import approval as AP
from ddflow.services.approvals import request as R
from ddflow.services.approvals.canon import Canon


@pytest.fixture(autouse=True)
def _a_person(monkeypatch):
    for var in ("DDFLOW_AGENT", *AP.HARNESS_MARKERS):
        monkeypatch.delenv(var, raising=False)


def _file(repo):
    log = EventLog(repo, "bot")
    st = fold(log.read_all(), strict=False)
    canon = Canon(verb="grant.add", agent="bot", item="T1", target="critic", state="s")
    return R.file(log, Config(), st, canon, reason="the reason")


def _ask_id(text: str) -> str:
    found = re.search(r"approval (Q[0-9a-f]+)", text)
    assert found, text
    return found.group(1)


def test_a_pending_request_lists_its_digest_and_answer_form(repo):
    run_cli(repo, "init")
    filed = _file(repo)
    code, out, err = run_cli(repo, "--json", "approvals", "pending")
    assert code == 0, err
    rows = json.loads(out)["approvals_pending"]
    assert [r["id"] for r in rows] == [filed.id]
    assert rows[0]["digest"] == filed.digest
    assert rows[0]["answer"] == f"ddflow approvals approve {filed.id}"

    code, out, err = run_cli(repo, "approvals", "pending")
    assert filed.digest[:12] in out, out
    assert f"ddflow approvals approve {filed.id}" in out, out
    # the notice the brief and the status render -- what an MCP/chat surface carries
    code, out, err = run_cli(repo, "status")
    assert filed.digest[:12] in out, out
    assert f"ddflow approvals approve {filed.id}" in out, out


def test_the_config_edit_class_has_a_request_route_an_agent_can_drive(repo):
    run_cli(repo, "init")
    code, out, err = run_cli(
        repo, "config", "ci.pins_precheck", "false", "--approval", "request", agent="bot"
    )
    assert code == 3 and "config.set" in err, err  # auto-filed by the door, still to answer
    code, out, _err = run_cli(repo, "--json", "config")
    values = {r["key"]: r["value"] for r in json.loads(out)["rows"]}
    assert values["ci.pins_precheck"] is True, "filing the request must not apply it"
    ask_id = _ask_id(err)
    row = asks_of(fold(EventLog(repo, "probe").read_all(), strict=False))[ask_id]
    assert row["verb"] == "config.set" and row["digest"]

    code, out, err = run_cli(
        repo, "config", "ci.pins_precheck", "false", "--approval", ask_id, agent="bot"
    )
    assert code == 3 and "not answered" in err
    digest = row["digest"]
    code, _o, err = run_cli(repo, "approvals", "approve", ask_id, "--digest", digest[:12])
    assert code == 0, err
    code, out, err = run_cli(
        repo, "--json", "config", "ci.pins_precheck", "false", "--approval", ask_id, agent="bot"
    )
    assert code == 0, err
    data = json.loads(out)
    assert data["approval"]["ask"] == ask_id and data["key"] == "ci.pins_precheck"
    code, out, _err = run_cli(repo, "--json", "config")
    values = {r["key"]: r["value"] for r in json.loads(out)["rows"]}
    assert values["ci.pins_precheck"] is False, "the answer applied the change"
    events = EventLog(repo, "probe").read_all()
    consumed = [e for e in events if e.kind == "ask.consumed" and e.data["id"] == ask_id]
    assert consumed, "the approval was not recorded as consumed"

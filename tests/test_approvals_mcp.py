"""RA-mcp (D-RA-6): over MCP an agent asks, lists its own asks, withdraws, and passes
`approval=<id>`; it can never answer. Adversarial: answers, other commands, replays, others' asks."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import approvals_answer as AA
from ddflow.core.handlers.asks import asks_of
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import approval as AP
from ddflow.surfaces.mcp import Server
from ddflow.surfaces.tools import TOOLS
from ddflow.surfaces.tools import families as F

VERB = "gate.skip.failed_review"


@pytest.fixture(autouse=True)
def _a_person(monkeypatch):
    for var in ("DDFLOW_AGENT", *AP.HARNESS_MARKERS):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def proj(repo):
    run_cli(repo, "init")
    for item in ("T1", "T2"):
        run_cli(repo, "task", "add", item, "--title", f"{item} fixture", "--globs", f"{item}.py")
        run_cli(repo, "gate", "record", item, "critic", "--outcome", "failed", "--reason", "x")
    return repo


def call(repo, tool, agent="bot", **args):
    args.setdefault("as_agent", agent)
    reply = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        }
    )
    res = reply["result"]
    text = res["content"][0]["text"]
    try:
        text = json.loads(text)
    except ValueError:
        pass
    refused = isinstance(text, dict) and "refusal" in text
    return text, bool(res.get("isError")) or refused


def ask(repo, agent="bot", item="T1"):
    out, err = call(
        repo,
        "ddflow_session",
        agent,
        action="ask",
        verb=VERB,
        item=item,
        target="critic",
        reason="down",
    )
    assert not err, out
    return out["id"] if isinstance(out, dict) else out


def person_approves(repo, ask_id, digest=None):
    row = asks_of(fold(EventLog(repo, "probe").read_all(), strict=False))[ask_id]
    return run_cli(repo, "approvals", "approve", ask_id, "--digest", (digest or row["digest"])[:12])


def skip(repo, item, ask_id, agent="bot"):
    return call(repo, "ddflow_gate", agent, action="skip", id=item, gate="critic",
                reason="the reviewer is down", approval=ask_id)  # fmt: skip


def test_the_session_family_carries_ask_asks_withdraw_and_no_answer_anywhere():
    members = F.FAMILIES["ddflow_session"].members
    assert {"ask", "asks", "withdraw"} <= set(members)
    names = " ".join([*TOOLS, *members]).lower()
    for word in ("approve", "deny", "revoke", "attest", "answer"):
        assert word not in names, word
    assert not any(n.startswith("ddflow_approvals") for n in TOOLS)


def test_ask_files_a_request_at_once_and_a_repeat_is_the_same_request(proj):
    first = ask(proj)
    assert first == ask(proj)
    row = asks_of(fold(EventLog(proj, "probe").read_all(), strict=False))[first]
    assert row["requested_by"] == "bot" and row["state"] == "pending"
    _out, err = call(proj, "ddflow_session", action="ask", verb="no.such.verb")
    assert err


def test_the_whole_scenario_over_mcp_and_the_approval_is_spent_once(proj):
    ask_id = ask(proj)
    _out, err = skip(proj, "T1", ask_id)
    assert err  # not approved yet
    assert person_approves(proj, ask_id)[0] == 0
    out, err = skip(proj, "T1", ask_id)
    assert not err, out
    run_cli(proj, "gate", "record", "T1", "critic", "--outcome", "failed", "--reason", "again")
    _again, err = skip(proj, "T1", ask_id)  # a replay of the spent approval on the same command
    assert err


def test_an_approval_for_command_a_is_refused_on_command_b(proj):
    ask_id = ask(proj, item="T1")
    assert person_approves(proj, ask_id)[0] == 0
    _out, err = skip(proj, "T2", ask_id)
    assert err
    state = fold(EventLog(proj, "probe").read_all(), strict=False)
    assert not [r for r in state.items["T2"].runs_of("critic") if r.outcome == "skipped"]
    assert skip(proj, "T1", ask_id)[1] is False  # the refusal on B spent nothing: A still works


def test_another_agent_cannot_spend_the_approval(proj):
    ask_id = ask(proj, "bot")
    assert person_approves(proj, ask_id)[0] == 0
    _out, err = skip(proj, "T1", ask_id, agent="mallory")
    assert err


def test_an_edited_digest_is_not_an_approval(proj):
    ask_id = ask(proj)
    code, _o, _e = person_approves(proj, ask_id, digest="0" * 12)
    assert code != 0
    assert skip(proj, "T1", ask_id)[1]


def test_nothing_over_mcp_can_answer(proj):
    ask_id = ask(proj)
    for action in ("approve", "deny", "revoke", "attest", "answer"):
        _out, err = call(proj, "ddflow_session", action=action, id=ask_id)
        assert err, action
    _out, err = call(proj, "ddflow_approvals", action="approve", id=ask_id)
    assert err
    digest = asks_of(fold(EventLog(proj, "probe").read_all(), strict=False))[ask_id]["digest"]
    assert AA.approvals_approve(proj, ask_id, digest=digest, agent="bot").exit != 0
    assert skip(proj, "T1", ask_id)[1]  # still unanswered
    # `approval` is only ever a way to name a request: an invented id or "request" spends nothing
    assert skip(proj, "T1", "Qdeadbeef")[1]


def test_withdraw_works_only_for_the_asker(proj):
    ask_id = ask(proj, "bot")
    _out, err = call(proj, "ddflow_session", "mallory", action="withdraw", id=ask_id)
    assert err
    row = asks_of(fold(EventLog(proj, "probe").read_all(), strict=False))[ask_id]
    assert row["state"] == "pending"
    out, err = call(proj, "ddflow_session", "bot", action="withdraw", id=ask_id)
    assert not err, out
    assert call(proj, "ddflow_session", "bot", action="withdraw", id=ask_id)[1]
    assert person_approves(proj, ask_id)[0] != 0  # a withdrawn ask cannot be answered


def test_asks_lists_only_the_callers_own_and_leaks_nothing_of_others(proj):
    mine, theirs = ask(proj, "bot", "T1"), ask(proj, "mallory", "T2")
    out, err = call(proj, "ddflow_session", "bot", action="asks")
    assert not err
    ids = [r["id"] for r in out["asks"]]
    assert ids == [mine]
    blob = json.dumps(out)
    assert theirs not in blob and "mallory" not in blob
    row = asks_of(fold(EventLog(proj, "probe").read_all(), strict=False))[theirs]
    assert row["digest"] not in blob
    out, _e = call(proj, "ddflow_session", "nobody", action="asks", all=True)
    assert out["asks"] == []

"""RA-cli: `ddflow approvals ...` -- a person answers from any terminal, an agent cannot.

The answer verbs are refused under every agent identity (a `--agent` flag, `DDFLOW_AGENT`, a
harness marker) before anything is recorded; the person says which request they read by its
digest; approve then lets the requesting agent spend it exactly once (RA-consume).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import approvals as API
from ddflow.config import Config
from ddflow.core.handlers.asks import asks_of
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import approval as AP
from ddflow.services.approvals import consume as C
from ddflow.services.approvals import request as R
from ddflow.services.approvals import state as PS
from ddflow.services.approvals.canon import Canon

VERB = "gate.skip.failed_review"


@pytest.fixture(autouse=True)
def _a_person(monkeypatch):
    for var in ("DDFLOW_AGENT", *AP.HARNESS_MARKERS):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def proj(repo):
    run_cli(repo, "init")
    return repo


def _state(repo):
    return fold(EventLog(repo, "probe").read_all(), strict=False)


def _canon(**over):
    base = {"verb": VERB, "agent": "bot", "item": "T1", "target": "critic", "state": "s1"}
    base.update(over)
    return Canon(**base)


def _file(repo, canon=None):
    log = EventLog(repo, "bot")
    return R.file(log, Config(), _state(repo), canon or _canon(), reason="needed")


def _approve(repo, filed, *extra, agent=""):
    return run_cli(
        repo, "approvals", "approve", filed.id, "--digest", filed.digest[:12], *extra, agent=agent
    )


def test_a_person_lists_shows_and_approves_and_the_agent_spends_it_once(proj):
    filed = _file(proj)
    code, out, _e = run_cli(proj, "--json", "approvals", "list")
    assert code == 0 and [r["id"] for r in json.loads(out)["approvals"]] == [filed.id]
    code, out, _e = run_cli(proj, "approvals", "show", filed.id)
    assert code == 0 and "Approve gated command" in out and filed.digest[:16] in out
    code, out, err = _approve(proj, filed)
    assert code == 0, (out, err)
    code, out, _e = run_cli(proj, "approvals", "list")
    assert "approved, 0/1 used" in out
    log = EventLog(proj, "bot")
    assert C.operator_gate(log, Config(), _canon(), approval_id=filed.id).ok
    code, out, _e = run_cli(proj, "approvals", "list")
    assert "no pending or unspent" in out  # spent: gone from the default list
    assert not C.operator_gate(log, Config(), _canon(), approval_id=filed.id).ok


def test_the_person_must_say_which_one_they_read(proj):
    filed = _file(proj)
    for digest in ("", "abc", "0" * 12, filed.digest[:7]):
        args = ["approvals", "approve", filed.id] + (["--digest", digest] if digest else [])
        code, _o, err = run_cli(proj, *args)
        assert code == 3 and "--digest" in err, (digest, err)
    assert asks_of(_state(proj))[filed.id]["state"] == "pending"


@pytest.mark.parametrize("verb", ["approve", "deny", "revoke"])
def test_an_agent_identity_cannot_answer_or_revoke(proj, verb):
    filed = _file(proj)
    args = ["approvals", verb, filed.id] + (
        ["--digest", filed.digest[:12]] if verb != "revoke" else []
    )
    code, _o, err = run_cli(proj, *args, agent="bot")
    assert code == 3 and "person's act" in err, err
    assert asks_of(_state(proj))[filed.id]["state"] == "pending"


@pytest.mark.parametrize("marker", ["CLAUDECODE", "DDFLOW_AGENT"])
def test_an_agent_environment_cannot_answer_either(proj, monkeypatch, marker):
    filed = _file(proj)
    monkeypatch.setenv(marker, "1")
    code, _o, err = _approve(proj, filed)
    assert code == 3 and "person's act" in err
    assert asks_of(_state(proj))[filed.id]["state"] == "pending"


def test_deny_blocks_the_spend_and_revoke_takes_an_approval_back(proj):
    denied = _file(proj, _canon(item="T2"))
    code, _o, err = run_cli(proj, "approvals", "deny", denied.id, "--digest", denied.digest[:12])
    assert code == 0, err
    log = EventLog(proj, "bot")
    got = C.operator_gate(log, Config(), _canon(item="T2"), approval_id=denied.id)
    assert not got.ok and "denied" in got.message
    filed = _file(proj, _canon(item="T3"))
    assert _approve(proj, filed)[0] == 0
    code, _o, err = run_cli(proj, "approvals", "revoke", filed.id, agent="bot")
    assert code == 3  # an agent cannot revoke
    code, _o, err = run_cli(proj, "approvals", "revoke", filed.id)
    assert code == 0, err
    got = C.operator_gate(log, Config(), _canon(item="T3"), approval_id=filed.id)
    assert not got.ok and "withdrawn" in got.message


def test_a_revoke_after_the_spend_is_refused_and_changes_nothing_already_done(proj):
    filed = _file(proj)
    assert _approve(proj, filed)[0] == 0
    log = EventLog(proj, "bot")
    assert C.operator_gate(log, Config(), _canon(), approval_id=filed.id, now=time.time()).ok
    code, _o, err = run_cli(proj, "approvals", "revoke", filed.id)
    assert code == 3 and "already spent" in err
    row = asks_of(_state(proj))[filed.id]
    assert row["used"] == 1 and row["state"] == "answered"


def test_an_agent_is_refused_before_the_ask_is_even_looked_up(proj):
    for verb in ("approve", "deny", "revoke"):
        code, _o, err = run_cli(proj, "approvals", verb, "Qdoesnotexist", agent="bot")
        assert code == 3 and "person's act" in err, (verb, err)  # not "no approval request"


def test_only_the_asker_withdraws_a_waiting_request(proj):
    filed = _file(proj)
    code, _o, err = run_cli(proj, "approvals", "withdraw", filed.id, agent="other-bot")
    assert code == 3, err
    code, _o, err = run_cli(proj, "approvals", "withdraw", filed.id, agent="bot")
    assert code == 0, err
    assert asks_of(_state(proj))[filed.id]["state"] == "withdrawn"


def test_requesting_by_hand_needs_a_verb_that_has_a_provider(proj, monkeypatch):
    code, _o, err = run_cli(proj, "approvals", "request", VERB, "--item", "T1", agent="bot")
    assert code != 0 and "cannot be requested by hand" in err
    monkeypatch.setitem(PS.PROVIDERS, VERB, lambda st, item, target, args: "state-1")
    out = API.approvals_request(proj, VERB, item="T1", target="critic", reason="x", agent="bot")
    assert out.exit == 0 and out.data["new"] is True
    again = API.approvals_request(proj, VERB, item="T1", target="critic", agent="bot")
    assert again.data["id"] == out.data["id"] and again.data["new"] is False


def test_no_mcp_tool_carries_the_approvals_verbs():
    root = Path(__file__).resolve().parents[1]
    # nothing the MCP server reaches imports the module that answers, and no tool is named for it
    mcp_side = [root / "ddflow/surfaces/mcp.py", *(root / "ddflow/surfaces/tools").rglob("*.py")]
    for p in mcp_side:
        if p.exists():
            text = p.read_text()
            assert "approvals_answer" not in text and "ddflow_approvals" not in text, p
    from ddflow.surfaces.declared import approvals as D

    assert D.COMMANDS and all(c.tool == "" for c in D.COMMANDS)  # CLI-only, every one
    tools = {c.tool for mod in _declared_modules() for c in mod.COMMANDS if c.tool}
    assert not [t for t in tools if "approvals" in t]


def _declared_modules():
    import importlib
    import pkgutil

    import ddflow.surfaces.declared as pkg

    for info in pkgutil.iter_modules(pkg.__path__):
        mod = importlib.import_module(f"{pkg.__name__}.{info.name}")
        if hasattr(mod, "COMMANDS"):
            yield mod


def test_an_answer_with_a_wrong_digest_does_not_print_the_right_one(proj):
    filed = _file(proj)
    code, _o, err = run_cli(proj, "approvals", "approve", filed.id, "--digest", "zzzzzzzz")
    assert code == 3 and filed.digest[:8] not in err


def test_the_answer_api_refuses_an_agent_without_reading_anything(tmp_path, monkeypatch):
    from ddflow.api import approvals_answer as AA

    def boom(*a, **k):
        raise AssertionError("an agent's call read the repo before it was refused")

    monkeypatch.setattr(AA, "_load", boom)
    nowhere = tmp_path / "no" / "such" / "repo"
    for out in (
        AA.approvals_approve(nowhere, "Q1", digest="abcdefgh", agent="bot"),
        AA.approvals_deny(nowhere, "Q1", digest="abcdefgh", agent="bot"),
        AA.approvals_revoke(nowhere, "Q1", agent="bot"),
    ):
        assert out.exit == 3 and "person's act" in out.reason
    assert AA.digest_chars(nowhere, "bot") == 0
    assert not nowhere.exists()

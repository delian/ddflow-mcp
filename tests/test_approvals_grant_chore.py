"""An agent adds a grant / approves the chore list with a person's approval (RA-consume-sites-2).

Same protocol as the gate skip: refused with a filed request and its id; a person answers
(`ddflow approvals approve`); the agent re-runs with `--approval ID`; the grant or approval is
recorded as the approver's with the stamp (`proxy`) naming the ask. Nothing else gets through.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.handlers.asks import asks_of
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import approval as AP

GRANT = ("grant", "add", "--item", "T1", "--gate", "critic", "--count", "1", "--reason", "one more")


@pytest.fixture(autouse=True)
def _a_person(monkeypatch):
    for var in ("DDFLOW_AGENT", *AP.HARNESS_MARKERS):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def proj(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "t.py")
    return repo


def _state(repo):
    return fold(EventLog(repo, "probe").read_all(), strict=False)


def _ask_id(err: str) -> str:
    word = err.split("approval ", 1)[1].split(maxsplit=1)[0]
    assert word.startswith("Q"), err
    return word


def _approve(repo, ask_id):
    digest = asks_of(_state(repo))[ask_id]["digest"]
    code, _o, err = run_cli(repo, "approvals", "approve", ask_id, "--digest", digest[:12])
    assert code == 0, err


def _granted(repo, subject_prefix=""):
    return [
        e for e in EventLog(repo, "probe").read_all()
        if e.kind == "approval.granted" and e.data.get("subject", "").startswith(subject_prefix)
    ]  # fmt: skip


def test_an_agent_adds_a_grant_with_a_persons_approval(proj):
    code, _o, err = run_cli(proj, *GRANT, agent="bot")
    assert code == 3 and "--approval" in err, err
    ask_id = _ask_id(err)
    assert asks_of(_state(proj))[ask_id]["verb"] == "grant.add"
    assert not _granted(proj, "grant:")
    _approve(proj, ask_id)
    code, out, err = run_cli(proj, *GRANT, "--approval", ask_id, agent="bot")
    assert code == 0, (out, err)
    (ev,) = _granted(proj, "grant:")
    assert ev.data["human"] is True and ev.data["proxy"]["ask"] == ask_id
    assert ev.data["proxy"]["proxy"] == "bot" and ev.data["proxy"]["level"] == 1
    code, out, _e = run_cli(proj, "--json", "grant", "list")
    assert [g["id"] for g in json.loads(out)["grants"]] == ["G1"]


def test_the_grant_approval_does_not_stretch_or_repeat(proj):
    ask_id = _ask_id(run_cli(proj, *GRANT, agent="bot")[2])
    _approve(proj, ask_id)
    bigger = list(GRANT)
    bigger[bigger.index("1")] = "3"  # a bigger grant than the one approved
    code, _o, err = run_cli(proj, *bigger, "--approval", ask_id, agent="bot")
    assert code == 3 and "other state" in err
    other_gate = list(GRANT)
    other_gate[other_gate.index("critic")] = "rubber_duck"
    assert run_cli(proj, *other_gate, "--approval", ask_id, agent="bot")[0] == 3
    assert not _granted(proj, "grant:")
    assert run_cli(proj, *GRANT, "--approval", ask_id, agent="bot")[0] == 0
    again = run_cli(proj, *GRANT, "--approval", ask_id, agent="bot")
    assert again[0] == 3  # spent, and a grant now exists: both make it another state
    assert len(_granted(proj, "grant:")) == 1


def test_a_person_at_a_terminal_adds_a_grant_as_before(proj):
    code, out, err = run_cli(proj, *GRANT)
    assert code == 0, (out, err)
    (ev,) = _granted(proj, "grant:")
    assert "proxy" not in ev.data


def test_an_agent_approves_the_chore_list_with_a_persons_approval(proj):
    code, _o, err = run_cli(proj, "chore", "approve", agent="bot")
    assert code == 3 and "--approval" in err, err
    ask_id = _ask_id(err)
    assert asks_of(_state(proj))[ask_id]["verb"] == "chore.approve"
    _approve(proj, ask_id)
    code, out, err = run_cli(proj, "chore", "approve", "--approval", ask_id, agent="bot")
    assert code == 0, (out, err)
    (ev,) = _granted(proj, "chore")
    assert ev.data["human"] is True and ev.data["proxy"]["ask"] == ask_id


def test_a_changed_chore_list_needs_a_new_approval(proj):
    ask_id = _ask_id(run_cli(proj, "chore", "approve", agent="bot")[2])
    _approve(proj, ask_id)
    cfgfile = proj / ".ddflow" / "config.toml"
    cfgfile.write_text(cfgfile.read_text() + '\n[chore]\nglobs = ["docs/**", "extra/**"]\n')
    code, _o, err = run_cli(proj, "chore", "approve", "--approval", ask_id, agent="bot")
    assert code == 3 and "other state" in err
    assert not _granted(proj, "chore")


def test_an_agent_cannot_use_another_verbs_approval_for_a_grant(proj):
    chore_id = _ask_id(run_cli(proj, "chore", "approve", agent="bot")[2])
    _approve(proj, chore_id)
    code, _o, err = run_cli(proj, *GRANT, "--approval", chore_id, agent="bot")
    assert code == 3 and "approval for 'chore.approve'" in err
    assert not _granted(proj, "grant:")

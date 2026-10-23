"""approve.partial by an agent (RA-consume-sites-3): `ddflow approve <id> <gate> --approval ID` accepts
a measured baseline-red partial once the operator approved exactly that outcome in chat."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from test_accept_partial_gate import _partial_item, _remeasure, _state

from ddflow.core.handlers.asks import asks_of
from ddflow.services import approval as AP


@pytest.fixture(autouse=True)
def _a_person(monkeypatch):
    for var in ("DDFLOW_AGENT", *AP.HARNESS_MARKERS):
        monkeypatch.delenv(var, raising=False)


def _ask_id(text: str) -> str:
    found = re.search(r"approval (Q[0-9a-f]+)", text)
    assert found, text
    return found.group(1)


def _answer(repo, ask_id):
    digest = asks_of(_state(repo))[ask_id]["digest"]
    assert run_cli(repo, "approvals", "approve", ask_id, "--digest", digest[:12])[0] == 0


def _accept(repo, *extra, agent="bot"):
    return run_cli(repo, "approve", "T1", "unit_tests", *extra, agent=agent)


def test_an_agent_accepts_a_partial_with_the_operators_approval(repo):
    _partial_item(repo)
    code, _o, err = _accept(repo)
    assert code != 0 and "--approval" in err and "approval Q" in err
    ask_id = _ask_id(err)
    assert asks_of(_state(repo))[ask_id]["verb"] == "approve.partial"
    _answer(repo, ask_id)
    code, out, err = _accept(repo, "--approval", ask_id)
    assert code == 0, (out, err)
    assert (
        _state(repo).items["T1"].gate_outcome("unit_tests") == "partial"
    )  # never turned into a pass
    assert run_cli(repo, "complete", "T1", "--model", "claude-opus-4")[0] == 0


def test_a_re_recorded_partial_needs_a_new_approval(repo):
    _partial_item(repo)
    ask_id = _ask_id(_accept(repo)[2])
    _answer(repo, ask_id)
    _remeasure(repo, "baseline-red: test_x again, bug B1")  # a different outcome: another digest
    code, _o, err = _accept(repo, "--approval", ask_id)
    assert code != 0 and "other state" in err


def test_the_agent_cannot_use_another_verbs_approval(repo):
    _partial_item(repo)
    other = _ask_id(run_cli(repo, "chore", "approve", agent="bot")[2])
    _answer(repo, other)
    code, _o, err = _accept(repo, "--approval", other)
    assert code != 0 and "approval for 'chore.approve'" in err
    assert _state(repo).items["T1"].gate_outcome("unit_tests") == "partial"


def test_a_person_accepts_a_partial_as_before(repo):
    _partial_item(repo)
    code, out, err = _accept(repo, agent="")
    assert code == 0, (out, err)

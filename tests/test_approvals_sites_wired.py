"""RA-consume-sites-3: the forced complete and the forced claim by an agent, through an operator
approval answered in chat. An agent may not use `--force` (the Bash hook), so the route is
`--approval request` -> a request id -> the operator answers -> `--approval ID`."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.handlers.asks import asks_of
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import approval as AP


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


def _ask_id(text: str) -> str:
    found = re.search(r"approval (Q[0-9a-f]+)", text)
    assert found, text
    return found.group(1)


def _approve(repo, ask_id):
    digest = asks_of(_state(repo))[ask_id]["digest"]
    code, _o, err = run_cli(repo, "approvals", "approve", ask_id, "--digest", digest[:12])
    assert code == 0, err


def _complete(repo, *extra, agent="bot"):
    return run_cli(repo, "complete", "T1", "--model", "claude-opus-4", *extra, agent=agent)


def test_complete_by_an_agent_over_unmet_conditions_needs_the_operators_approval(proj):
    code, _o, err = _complete(proj)
    assert code == 3 and "--approval request" in err and "--force" in err
    assert not asks_of(_state(proj))  # a routine refusal files nothing
    code, _o, err = _complete(proj, "--approval", "request")
    assert code == 3
    ask_id = _ask_id(err)
    assert asks_of(_state(proj))[ask_id]["verb"] == "complete.force"
    code, _o, err = _complete(proj, "--approval", ask_id)
    assert code == 3 and "not answered" in err
    _approve(proj, ask_id)
    code, out, err = _complete(proj, "--approval", ask_id)
    assert code == 0, (out, err)
    ev = [e for e in EventLog(proj, "probe").read_all() if e.kind == "item.completed"][-1]
    assert ev.data["forced"] is True and ev.data["overridden"]
    assert ev.data["approval"]["ask"] == ask_id and ev.data["approval"]["proxy"] == "bot"


def test_the_complete_approval_does_not_stretch_to_other_state_or_repeat(proj):
    ask_id = _ask_id(_complete(proj, "--approval", "request")[2])
    _approve(proj, ask_id)
    run_cli(
        proj, "gate", "skip", "T1", "research", "--reason", "n/a here"
    )  # fewer unmet conditions
    code, _o, err = _complete(proj, "--approval", ask_id)
    assert code == 3 and "other state" in err
    assert _state(proj).items["T1"].state != "done"


def test_a_failed_review_gate_is_not_lifted_by_an_approval(proj):
    assert run_cli(proj, "gate", "record", "T1", "critic", "--outcome", "failed",
                   "--reason", "findings")[0] == 0  # fmt: skip
    code, _o, err = _complete(proj, "--approval", "request")
    assert code == 3 and "failed with findings not re-reviewed" in err
    assert not asks_of(_state(proj))


def test_a_person_keeps_force_and_an_approval_is_an_agents_route(proj):
    code, _o, err = run_cli(
        proj, "complete", "T1", "--model", "claude-opus-4", "--approval", "request"
    )
    assert code == 3 and "agent's route" in err
    code, out, err = run_cli(proj, "complete", "T1", "--model", "claude-opus-4", "--force")
    assert code == 0, (out, err)


def test_claim_force_by_approval_for_an_agent(proj, monkeypatch):
    """The refusals `--force` overrides need a PR in review or another clone's lease; here the
    first attempt is made to refuse in that way, and the approval mechanics are the real ones."""
    import importlib

    from ddflow.api import claim as CL

    C = importlib.import_module("ddflow.api.lifecycle.claim")
    from ddflow.core import outcome as O

    real = C._claim_once
    calls = []

    def once(repo, item, *, force=False, **kw):
        calls.append(force)
        if not force:
            return O.refused(
                "item.claimed",
                f"{item} is in review; --force to take it back anyway.",
                id=item,
                alternatives=[],
            )
        return real(repo, item, force=force, **kw)

    monkeypatch.setattr(C, "_claim_once", once)
    out = CL(proj, "T1", no_worktree=True, agent="bot")
    assert out.exit == 3 and "--approval request" in out.reason  # the plain refusal is unchanged
    out = CL(proj, "T1", no_worktree=True, agent="bot", approval="request")
    assert out.exit == 3 and "approval Q" in out.reason
    ask_id = _ask_id(out.reason)
    assert asks_of(_state(proj))[ask_id]["verb"] == "claim.force"
    assert CL(proj, "T1", no_worktree=True, agent="bot", approval=ask_id).exit == 3  # not answered
    _approve(proj, ask_id)
    out = CL(proj, "T1", no_worktree=True, agent="bot", approval=ask_id)
    assert out.exit == 0, out.reason
    assert calls[-1] is True
    assert _state(proj).items["T1"].lease.holder == "bot"
    # a person has --force: the approval route changes nothing for them
    out = CL(proj, "T1", no_worktree=True, agent="", approval="request")
    assert (
        out.exit == 3 and "approval" not in out.reason
    )  # the refusal is the plain one: no route offered
    assert len([r for r in asks_of(_state(proj)).values() if r["verb"] == "claim.force"]) == 1

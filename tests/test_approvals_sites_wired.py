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
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "t.py")
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


def test_an_explicit_approval_request_is_not_auto_answered_by_a_standing_rule(proj):
    """Bef8d328af3: with `complete_unavailable` on and the standing window open, `complete
    --approval request` must file a request and refuse -- never complete FORCED on its own."""
    cfgfile = proj / ".ddflow" / "config.toml"
    cfgfile.write_text(
        cfgfile.read_text()
        + '\n[approvals]\nstanding_window_s = 86400\npolicy = ["complete_unavailable"]\n'
    )
    code, out, err = _complete(proj, "--approval", "request")
    assert code == 3, (out, err)
    assert _state(proj).items["T1"].state != "done"
    ask_id = _ask_id(err)
    row = asks_of(_state(proj))[ask_id]
    assert row["verb"] == "complete.force" and row["policy"] is False
    assert row["answered_by"] == ""


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
    assert asks_of(_state(proj))[ask_id]["used"] == 1  # the landed forced claim recorded the use
    # a person has --force: the approval route changes nothing for them
    out = CL(proj, "T1", no_worktree=True, agent="", approval="request")
    assert out.exit == 3
    assert out.reason == "T1 is in review; --force to take it back anyway."  # plain, no route
    assert len([r for r in asks_of(_state(proj)).values() if r["verb"] == "claim.force"]) == 1


def test_a_refused_forced_claim_does_not_spend_the_operators_approval(proj, monkeypatch):
    """B6365c86730: `claim --approval ID` spent (recorded `ask.consumed`) the operator's one-use
    `claim.force` approval the moment it was checked, then ran the forced claim -- which still
    refused (a live lease overlap `--force` cannot break). The person's answer was lost and had
    to be asked for again. The use must be recorded only once the claim has actually landed."""
    import importlib

    from ddflow.api import claim as CL
    from ddflow.core import outcome as O

    C = importlib.import_module("ddflow.api.lifecycle.claim")

    def once(repo, item, *, force=False, **kw):
        if not force:
            return O.refused(
                "item.claimed",
                f"{item} is in review; --force to take it back anyway.",
                id=item,
                alternatives=[],
            )
        # the forced claim STILL refuses: a live lease overlap `--force` cannot break
        return O.refused(
            "item.claimed",
            f"{item} overlaps sw-fleet's live lease on DX-fleet-registry.",
            id=item,
            alternatives=[],
        )

    monkeypatch.setattr(C, "_claim_once", once)
    out = CL(proj, "T1", no_worktree=True, agent="bot", approval="request")
    ask_id = _ask_id(out.reason)
    _approve(proj, ask_id)
    out = CL(proj, "T1", no_worktree=True, agent="bot", approval=ask_id)
    assert out.exit == 3, out.reason
    row = asks_of(_state(proj))[ask_id]
    assert row["used"] == 0, row  # the refused forced claim spent nothing
    assert not row.get("consumed"), row  # no `ask.consumed` was recorded
    assert "was NOT consumed" in out.reason, out.reason


def test_a_standing_policy_claim_force_over_an_empty_approval_still_lands(proj, monkeypatch):
    """B6365c86730 reviewer follow-up: the EMPTY `--approval` path (no `--approval`, no `--force`)
    must still route through `operator_gate`, where a standing rule (`claim_unavailable` over a
    lapsed lease) auto-approves `claim.force` and runs the forced claim. A collapse of that branch
    to an unconditional refusal silently breaks the documented lapsed-lane recovery flow."""
    import importlib

    from ddflow.api import claim as CL
    from ddflow.core import outcome as O
    from ddflow.infra.log import EventLog

    C = importlib.import_module("ddflow.api.lifecycle.claim")

    cfgfile = proj / ".ddflow" / "config.toml"
    cfgfile.write_text(
        cfgfile.read_text()
        + '\n[approvals]\npolicy = ["claim_unavailable"]\npolicy_max_uses = 99\n'
    )
    EventLog(proj, "seed").append(
        "lease.acquired",
        "T1",
        {"holder": "gone", "at": 0.0, "ttl_s": 1, "globs": ["t.py"]},
    )

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
        return O.ok("item.claimed", id=item)

    monkeypatch.setattr(C, "_claim_once", once)
    out = CL(proj, "T1", no_worktree=True, agent="bot")
    assert out.exit == 0, out.reason
    assert calls == [False, True], calls  # the standing rule drove a FORCED claim, not a refusal


def test_an_agents_force_flag_never_completes_even_with_approval_request(proj):
    """B49fa10bbb1: `force` plus `--approval request` skipped the approval check, so an agent
    (every MCP call is one) force-completed. The flag is a person's; an agent's is ignored."""
    for extra in (("--force",), ("--force", "--approval", "request")):
        code, _o, err = _complete(proj, *extra)
        assert code == 3, err
        assert _state(proj).items["T1"].state != "done"
    ask_id = _ask_id(err)  # the combined call filed the request, as `--approval request` does
    assert asks_of(_state(proj))[ask_id]["verb"] == "complete.force"
    code, _o, err = _complete(proj, "--force", "--approval", ask_id)
    assert code == 3 and "not answered" in err


def test_over_mcp_force_with_approval_request_files_a_request_and_refuses(proj):
    from ddflow.api import lifecycle as LC
    from ddflow.services import identity as ID

    with ID.via_mcp():
        out = LC.complete(proj, "T1", model="claude-opus-4", force=True, approval="request")
    assert out.exit == 3, out.reason
    assert _state(proj).items["T1"].state != "done"
    assert [r["verb"] for r in asks_of(_state(proj)).values()] == ["complete.force"]


def test_an_agents_claim_force_is_not_a_bypass(proj, monkeypatch):
    import importlib

    from ddflow.core import outcome as O

    C = importlib.import_module("ddflow.api.lifecycle.claim")
    calls = []

    def once(repo, item, *, force=False, **kw):
        calls.append(force)
        if not force:
            return O.refused(
                "item.claimed", f"{item} is in review; --force.", id=item, alternatives=[]
            )
        return O.ok("item.claimed", id=item)

    monkeypatch.setattr(C, "_claim_once", once)
    out = C.claim(proj, "T1", no_worktree=True, agent="bot", force=True, approval="request")
    assert out.exit == 3 and "approval Q" in out.reason
    assert calls and not any(calls)


def test_config_and_phase_approval_refusals_name_one_refusal(proj):
    """B-config-phase-doubled-refusing-prefix: `config --approval request` and
    `phase complete --approval request` each stacked a second 'refusing: ' onto
    `consume.operator_gate`'s already-prefixed message (the stack fixed for the
    complete.force path)."""
    code, _o, err = run_cli(
        proj, "config", "ci.pins_precheck", "false", "--approval", "request", agent="bot"
    )
    assert code == 3, err
    assert "refusing: " in err and "refusing: refusing: " not in err, err
    # A real id the spend refuses is another already-prefixed path the old caller doubled.
    code, _o, err = run_cli(
        proj, "config", "ci.pins_precheck", "false", "--approval", "Qnope", agent="bot"
    )
    assert code == 3, err
    assert "refusing: " in err and "refusing: refusing: " not in err, err

    log = EventLog(proj, "operator")
    log.append("phase.added", "P9", {"title": "planned"})
    log.append("task.added", "P9.T1", {"parent": "P9", "title": "one"})
    log.append("item.completed", "P9.T1", {"kind": "task", "evidence": "done"})
    code, _o, err = run_cli(proj, "phase", "complete", "P9", "--approval", "request", agent="bot")
    assert code == 3, err
    assert "refusing: " in err and "refusing: refusing: " not in err, err

    # `operator_gate` owns the 'refusing: ' prefix on every refusal, steering included
    # (B-consume-refusal-prefix-owner), so it still carries exactly one, never doubled.
    code, _o, err = run_cli(proj, "phase", "complete", "P9", agent="bot")
    assert code == 3, err
    assert "refusing: " in err and "refusing: refusing: " not in err, err

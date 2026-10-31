"""The last terminal-only acts, routed (RA-consume-sites-4): `export --force`, `export --local`,
and the relayed-consent verbs `upgrade --self` and `plan confirm` (D-RA-Q2).

Same protocol as every routed site: an agent is refused with a filed request and its id; a person
answers; the agent re-runs with `--approval ID`. Adversarial cases: an approval for one site or
document is refused on another, an agent cannot answer for itself, a spent or edited approval is
refused. Relayed consent still works for the two verbs, but is recorded and warned about."""

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
from ddflow.infra import proc as P
from ddflow.infra.log import EventLog
from ddflow.services import approval as AP
from ddflow.services import selfupgrade as SU
from ddflow.services.approvals import audit as AU
from ddflow.services.approvals import relayed as RL
from ddflow.services.approvals import sites as S


@pytest.fixture(autouse=True)
def _a_person(monkeypatch):
    for var in ("DDFLOW_AGENT", *AP.HARNESS_MARKERS):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def proj(repo):
    run_cli(repo, "init")
    return repo


def _log(repo):
    return EventLog(repo, "probe")


def _state(repo):
    return fold(_log(repo).read_all(), strict=False)


def _ask_id(err: str) -> str:
    found = re.search(r"approval (Q[0-9a-f]+)", err)
    assert found, err
    return found.group(1)


def _plan(repo, *argv, agent=""):
    """`ddflow plan confirm ...`: (code, stdout + stderr), the refusal is printed on either."""
    code, out, err = run_cli(repo, "plan", "confirm", *argv, agent=agent)
    return code, out + err


def _approve(repo, ask_id):
    digest = asks_of(_state(repo))[ask_id]["digest"]
    code, _o, err = run_cli(repo, "approvals", "approve", ask_id, "--digest", digest[:12])
    assert code == 0, err


# -- the table ------------------------------------------------------------------------------


def test_the_last_sites_are_routed_and_none_is_left_unrouted():
    assert S.NOT_YET_ROUTED == {}
    for verb, tier in (("export.force", "T2"), ("export.machine", "T2")):
        assert S.site(verb).tier == tier and S.site(verb).min_level == S.L1
    for verb in ("upgrade.self", "plan.confirm"):
        assert S.site(verb).tier == S.T2 and S.site(verb).functions  # routed: a marker call
    # the one site that cannot be routed says so, and why
    skew = S.site("skew.override")
    assert skew.tier == S.T2 and "refuses the approval engine's own writes" in skew.digest


# -- export --force -------------------------------------------------------------------------


def _hand_edited(repo):
    run_cli(repo, "export", "changelog", "--update", "--yes")
    target = repo / "CHANGELOG.md"
    target.write_text(target.read_text() + "\nhand edit\n")
    return target


FORCE = ("export", "changelog", "--update", "--yes", "--force")


def test_an_agent_forces_an_export_only_with_a_persons_approval(proj):
    target = _hand_edited(proj)
    code, _o, err = run_cli(proj, *FORCE, agent="bot")
    assert code == 3 and "--approval" in err, err
    assert "hand edit" in target.read_text()
    ask_id = _ask_id(err)
    assert asks_of(_state(proj))[ask_id]["verb"] == "export.force"
    assert run_cli(proj, *FORCE, "--approval", ask_id, agent="bot")[0] == 3  # not answered yet
    _approve(proj, ask_id)
    code, out, err = run_cli(proj, *FORCE, "--approval", ask_id, agent="bot")
    assert code == 0, (out, err)
    assert "hand edit" not in target.read_text()
    assert run_cli(proj, *FORCE, "--approval", ask_id, agent="bot")[0] == 3  # spent


def test_a_person_forces_an_export_as_before(proj):
    target = _hand_edited(proj)
    code, out, err = run_cli(proj, *FORCE)
    assert code == 0, (out, err)
    assert "hand edit" not in target.read_text()


def test_a_force_approval_is_bound_to_the_file_it_saw(proj):
    target = _hand_edited(proj)
    ask_id = _ask_id(run_cli(proj, *FORCE, agent="bot")[2])
    _approve(proj, ask_id)
    target.write_text(target.read_text() + "another hand edit after the approval\n")
    code, _o, err = run_cli(proj, *FORCE, "--approval", ask_id, agent="bot")
    assert code == 3 and "other state" in err, err
    assert "another hand edit" in target.read_text()


def test_an_approval_for_another_site_does_not_force_an_export(proj):
    target = _hand_edited(proj)
    # a person's approval of a LOCAL selection (a different verb) is no approval to force
    local = run_cli(proj, "export", "enable", "changelog", "--local", agent="bot")
    other = _ask_id(local[2])
    _approve(proj, other)
    code, _o, err = run_cli(proj, *FORCE, "--approval", other, agent="bot")
    assert code == 3 and "export.machine" in err and "not 'export.force'" in err, err
    assert "hand edit" in target.read_text()


def test_a_force_approval_does_not_carry_to_another_document(proj):
    target = _hand_edited(proj)
    ask_id = _ask_id(run_cli(proj, *FORCE, agent="bot")[2])
    _approve(proj, ask_id)
    code, _o, err = run_cli(proj, "export", "bugs", "--update", "--yes", "--force",
                            "--approval", ask_id, agent="bot")  # fmt: skip
    assert code == 3, err
    assert "hand edit" in target.read_text()


def test_an_agent_cannot_answer_its_own_force_request(proj):
    _hand_edited(proj)
    ask_id = _ask_id(run_cli(proj, *FORCE, agent="bot")[2])
    digest = asks_of(_state(proj))[ask_id]["digest"]
    code, _o, err = run_cli(proj, "approvals", "approve", ask_id, "--digest", digest[:12],
                            agent="bot")  # fmt: skip
    assert code != 0, err
    assert asks_of(_state(proj))[ask_id]["state"] == "pending"


# -- export --local -------------------------------------------------------------------------


LOCAL = ("export", "enable", "changelog", "--local")


def test_an_agent_selects_a_document_per_machine_only_with_approval(proj):
    code, _o, err = run_cli(proj, *LOCAL, agent="bot")
    assert code == 3 and "--approval" in err, err
    ask_id = _ask_id(err)
    assert asks_of(_state(proj))[ask_id]["verb"] == "export.machine"
    assert not (proj / ".ddflow" / "local" / "config.toml").exists() or (
        "changelog" not in (proj / ".ddflow" / "local" / "config.toml").read_text()
    )
    _approve(proj, ask_id)
    # the approval is of THIS action: a disable on the same document is another command
    code, _o, err = run_cli(proj, "export", "disable", "changelog", "--local",
                            "--approval", ask_id, agent="bot")  # fmt: skip
    assert code == 3 and "other state" in err, err
    code, out, err = run_cli(proj, *LOCAL, "--approval", ask_id, agent="bot")
    assert code == 0, (out, err)
    assert "changelog" in (proj / ".ddflow" / "local" / "config.toml").read_text()


def test_a_shared_enable_by_an_agent_still_needs_no_approval(proj):
    code, out, err = run_cli(proj, "export", "enable", "changelog", agent="bot")
    assert code == 0, (out, err)


def test_a_person_selects_per_machine_as_before(proj):
    assert run_cli(proj, *LOCAL)[0] == 0


# -- plan confirm ---------------------------------------------------------------------------


PLAN = """
[PT1]
title = "plan task"
globs = ["src/a.py"]
acceptance = "it works"
"""


@pytest.fixture
def planned(proj):
    plan = proj / "plan.toml"
    plan.write_text(PLAN)
    code, out, err = run_cli(proj, "--json", "plan", "apply", str(plan), "--plan-id", "P1")
    assert code == 0, (out, err)
    digest = json.loads(out)["hash"]
    code, out, err = run_cli(
        proj, "plan", "apply", str(plan), "--apply", "--plan-id", "P1", "--hash", digest
    )
    assert code == 0, (out, err)
    return proj


def _unblock_notes(repo):
    return [e.data.get("note", "") for e in _log(repo).read_all() if e.kind == "item.unblocked"]


def test_an_agent_confirms_a_plan_with_a_persons_approval(planned):
    code, err = _plan(planned, "P1", agent="bot")
    assert code == 3 and "--approval" in err, err
    ask_id = _ask_id(err)
    assert asks_of(_state(planned))[ask_id]["verb"] == "plan.confirm"
    assert not _unblock_notes(planned)
    _approve(planned, ask_id)
    code, out, err = run_cli(planned, "plan", "confirm", "P1", "--approval", ask_id, agent="bot")
    assert code == 0, (out, err)
    (note,) = _unblock_notes(planned)
    assert f"approval {ask_id}" in note and RL.RELAYED_TAG not in note
    assert not [w for w in AU.findings(_state(planned), Config(), events=_log(planned).read_all())
                if "RELAYED" in w]  # fmt: skip


def test_a_plan_approval_is_for_that_plan_and_that_verb_only(planned):
    ask_id = _ask_id(_plan(planned, "P1", agent="bot")[1])
    _approve(planned, ask_id)
    code, err = _plan(planned, "P2", "--approval", ask_id, agent="bot")
    assert code in (2, 3), err  # another plan: nothing waiting there, or another state
    assert not _unblock_notes(planned)
    grant = run_cli(planned, "grant", "add", "--item", "PT1", "--gate", "critic",
                    "--count", "1", "--reason", "x", "--approval", ask_id, agent="bot")  # fmt: skip
    assert grant[0] == 3 and "not 'grant.add'" in grant[2], grant[2]


def test_relayed_consent_still_works_but_is_recorded_and_warned(planned):
    code, out, err = run_cli(planned, "plan", "confirm", "P1", "--yes", "--reason", "chat said ok",
                             agent="bot")  # fmt: skip
    assert code == 0, (out, err)
    (note,) = _unblock_notes(planned)
    assert RL.RELAYED_TAG in note and "chat said ok" in note
    code, out, err = run_cli(planned, "approvals", "log", agent="bot")
    assert code == 0 and "plan.confirm by bot" in out and "RELAYED consent" in out, (out, err)


def test_require_approval_ends_the_relayed_exception_for_a_verb(planned):
    cfg = planned / ".ddflow" / "local" / "config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('[approvals]\nrequire_approval = ["plan.confirm"]\n')
    code, err = _plan(planned, "P1", "--yes", "--reason", "chat said ok", agent="bot")
    assert code == 3 and "approval" in err, err
    assert not _unblock_notes(planned)
    # a person at a terminal is unaffected
    assert run_cli(planned, "plan", "confirm", "P1", "--yes", "--reason", "i am here")[0] == 0


def test_an_agent_without_consent_or_approval_gets_the_route_not_a_dead_end(planned):
    code, err = _plan(planned, "P1", agent="bot")
    assert code == 3 and "wait for the operator" in err and "--approval" in err
    assert not _unblock_notes(planned)


def test_a_person_without_consent_gets_the_old_refusal(planned):
    code, err = _plan(planned, "P1")
    assert code == 3 and "--yes --reason" in err, err


# -- upgrade --self -------------------------------------------------------------------------


class Installer:
    """A runner that records argv: no network, no real installer."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, argv, timeout_s):
        self.calls.append(argv)
        return P.Captured(0, "installed\n", "")


@pytest.fixture(autouse=True)
def _state_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


def _up(repo, installer, *, agent="bot", **kw):
    """`upgrade --self` as the surfaces call it, by ``agent`` ("" = a person at a terminal)."""
    base = {"method": "pip-venv", "running": "0.1.9", "target": "9.9.9", "surface": "cli"}
    req = SU.Request(**{**base, "agent": agent, **kw})
    log = EventLog(repo, agent or "person")
    return SU.run(repo, Config.load(repo), log, req, runner=installer)


def test_an_agent_upgrades_ddflow_with_a_persons_approval(proj):
    inst = Installer()
    out = _up(proj, inst)
    assert out["exit"] == 3 and "--approval" in out["text"], out
    ask_id = _ask_id(out["text"])
    assert asks_of(_state(proj))[ask_id]["verb"] == "upgrade.self" and not inst.calls
    assert _up(proj, inst, approval=ask_id)["exit"] == 3  # not answered yet
    _approve(proj, ask_id)
    other = _up(proj, inst, approval=ask_id, target="9.9.8")
    assert other["exit"] == 3 and "other state" in other["text"] and not inst.calls
    done = _up(proj, inst, approval=ask_id)
    assert done["exit"] == 0 and len(inst.calls) >= 1, done
    calls = len(inst.calls)
    assert _up(proj, inst, approval=ask_id)["exit"] == 3  # spent
    assert len(inst.calls) == calls
    (rec,) = [e for e in _log(proj).read_all() if e.kind == "upgrade.applied"]
    assert f"approval {ask_id}" in str(rec.data["confirmed"])
    assert RL.RELAYED_TAG not in str(rec.data["confirmed"])


def test_relayed_self_upgrade_is_recorded_and_warned(proj):
    inst = Installer()
    out = _up(proj, inst, confirm=["self"], reason="operator said so")
    assert out["exit"] == 0 and inst.calls, out
    events = [e for e in _log(proj).read_all() if e.kind == "upgrade.applied"]
    assert RL.RELAYED_TAG in str(events[-1].data["confirmed"])
    warned = AU.findings(_state(proj), Config(), events=events)
    assert any("upgrade.self by bot" in w and "RELAYED" in w for w in warned), warned


def test_a_person_upgrades_with_consent_as_before(proj):
    inst = Installer()
    out = _up(proj, inst, agent="", confirm=["self"], reason="me")
    assert out["exit"] == 0 and inst.calls, out
    events = [e for e in _log(proj).read_all() if e.kind == "upgrade.applied"]
    assert RL.RELAYED_TAG not in str(events[-1].data["confirmed"])
    assert _up(proj, Installer(), agent="")["exit"] == 3  # no consent: the old refusal


def test_require_approval_ends_the_relayed_self_upgrade(proj):
    cfg = proj / ".ddflow" / "local" / "config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('[approvals]\nrequire_approval = ["upgrade.self"]\n')
    inst = Installer()
    out = _up(proj, inst, confirm=["self"], reason="operator said so")
    assert out["exit"] == 3 and "--approval" in out["text"] and not inst.calls, out


def test_a_self_upgrade_approval_does_not_confirm_a_plan(planned):
    ask_id = _ask_id(_up(planned, Installer())["text"])
    _approve(planned, ask_id)
    code, err = _plan(planned, "P1", "--approval", ask_id, agent="bot")
    assert code == 3 and "not 'plan.confirm'" in err, err
    assert not _unblock_notes(planned)


def test_the_cli_and_the_tool_carry_the_approval_to_the_request():
    from ddflow.surfaces.declared.setup import _self_request

    req = _self_request({"self": "run", "approval": "Q1"}, "bot")
    assert (req.agent, req.approval, req.surface) == ("bot", "Q1", "mcp")
    code, _o, err = run_cli(Path("."), "upgrade", "--approval", "Q1")
    assert code == 3 and "--approval only go with --self" in err, err


# -- the skew override ----------------------------------------------------------------------


def test_every_skew_override_is_warned_about():
    class St:
        skew_overrides = ({"agent": "bot", "at": "2026-10-31T10:00:00Z", "reason": "chat said so"},)

    (w,) = RL.uses([], St())
    assert "skew.override by bot" in w and "relayed consent" in w and "chat said so" in w

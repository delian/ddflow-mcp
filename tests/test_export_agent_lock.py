"""An agent may lock (veto) an export document; the operator can always unlock
(B-export-agent-lock, operator answer 2026-10-03)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import run_cli
from test_export_select import _call, _ev, _events, proj, run_human  # noqa: F401

from ddflow.core import model


def _lock(proj, agent="bot"):
    return run_cli(proj, "export", "disable", "roadmap", "--lock", agent=agent)


def test_an_agent_lock_is_accepted_and_records_the_agent(proj):
    code, out, err = _lock(proj)
    assert code == 0, (out, err)
    (ev,) = _events(proj, "export.disabled")
    assert ev["data"]["locked"] is True and ev["data"]["human"] is False
    assert ev["data"]["by"] == "bot"
    listing = run_cli(proj, "export")[1]
    assert "locked by agent bot" in listing


def test_another_agents_enable_is_refused_with_the_reason(proj):
    assert _lock(proj)[0] == 0
    code, _o, err = run_cli(proj, "export", "enable", "roadmap", agent="other")
    assert code == 3 and "locked by agent bot" in err
    assert _call(proj, {"action": "enable", "doc": "roadmap"})["exit"] == 3
    assert _events(proj, "export.enabled") == []


def test_the_locking_agent_may_lift_its_own_lock(proj):
    assert _lock(proj)[0] == 0
    assert run_cli(proj, "export", "enable", "roadmap", agent="bot")[0] == 0
    assert run_cli(proj, "export", "enable", "roadmap", agent="other")[0] == 0


def test_the_operator_unlocks_an_agent_lock(proj):
    assert _lock(proj)[0] == 0
    assert run_human(proj, "export", "enable", "roadmap")[0] == 0
    assert run_cli(proj, "export", "enable", "roadmap", agent="other")[0] == 0


def test_an_agent_lock_never_overrides_or_downgrades_an_operator_lock(proj):
    assert run_human(proj, "export", "disable", "roadmap", "--lock")[0] == 0
    assert _lock(proj)[0] == 0
    assert len(_events(proj, "export.disabled")) == 1  # nothing new: already locked
    code, _o, err = run_cli(proj, "export", "enable", "roadmap", agent="bot")
    assert code == 3 and "locked by the operator" in err  # the locking agent too


def test_an_operator_lock_replaces_an_agent_lock(proj):
    assert _lock(proj)[0] == 0
    assert run_human(proj, "export", "disable", "roadmap", "--lock")[0] == 0
    code, _o, err = run_cli(proj, "export", "enable", "roadmap", agent="bot")
    assert code == 3 and "locked by the operator" in err


def test_an_agent_enable_after_the_operator_unlock_is_not_blocked_by_the_old_lock(proj):
    assert _lock(proj)[0] == 0
    assert run_human(proj, "export", "enable", "roadmap")[0] == 0
    assert run_cli(proj, "export", "disable", "roadmap", agent="bot")[0] == 0
    assert run_cli(proj, "export", "enable", "roadmap", agent="other")[0] == 0


def test_an_agent_still_cannot_acknowledge_or_unlock_an_operator_lock(proj):
    assert run_human(proj, "export", "disable", "roadmap", "--lock")[0] == 0
    assert run_cli(proj, "export", "enable", "roadmap", agent="bot")[0] == 3
    assert run_cli(proj, "export", "ack", agent="bot")[0] in (0, 3)


def _fold(*events):
    return model.fold(list(events)).exports["roadmap"]


def test_the_fold_keeps_the_first_lock_and_a_forged_enable_lifts_nothing():
    e = _fold(
        _ev("export.disabled", "roadmap", 1, by="a1", human=False, locked=True),
        _ev("export.disabled", "roadmap", 2, by="a2", human=False, locked=True),
    )
    assert e["locked"] and e["lock"]["by"] == "a1" and not e["lock"]["human"]
    e = _fold(
        _ev("export.disabled", "roadmap", 1, by="a1", human=False, locked=True),
        _ev("export.enabled", "roadmap", 2, by="a2"),  # another agent: the lock stays
    )
    assert e["locked"]
    e = _fold(
        _ev("export.disabled", "roadmap", 1, by="op", human=True, locked=True),
        _ev("export.disabled", "roadmap", 2, by="a1", human=False, locked=True),
        _ev("export.enabled", "roadmap", 3, by="a1"),  # an agent never lifts the operator's
    )
    assert e["locked"] and e["lock"]["human"] is True
    e = _fold(
        _ev("export.disabled", "roadmap", 1, by="a1", human=False, locked=True),
        _ev("export.disabled", "roadmap", 2, by="op", human=True, locked=True),
    )
    assert e["lock"]["human"] is True and e["lock"]["by"] == "op"
    e = _fold(
        _ev("export.disabled", "roadmap", 1, by="a1", human=False, locked=True),
        _ev("export.enabled", "roadmap", 2, by="op", human=True),
    )
    assert not e["locked"]

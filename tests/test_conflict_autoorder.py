"""ddflow orders colliding items itself after a lease deadlock (DX-conflict-autoorder)."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api.reporting import wait_cycles
from ddflow.config import Config
from ddflow.core import autoorder as AO
from ddflow.core.graph import find_cycles
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import autoorder as AOS

AGENTS = ["agent-a", "agent-b", "agent-c"]


def _setup(repo, n: int) -> None:
    assert run_cli(repo, "init", agent=AGENTS[0])[0] == 0
    for i in range(1, n + 1):
        run_cli(
            repo,
            "task",
            "add",
            f"T{i}",
            "--title",
            f"T{i}",
            "--globs",
            f"src/f{i}.py",
            agent=AGENTS[0],
        )
    for i in range(1, n + 1):
        assert run_cli(repo, "claim", f"T{i}", "--no-worktree", agent=AGENTS[i - 1])[0] == 0
    for i in range(1, n + 1):  # each widens onto the next one's file: a ring
        j = i % n + 1
        run_cli(repo, "update", f"T{i}", "--globs", f"src/f{i}.py,src/f{j}.py", agent=AGENTS[i - 1])


def _mode(repo, mode: str) -> None:
    assert run_cli(repo, "config", "schedule.auto_order", mode, agent=AGENTS[0])[0] == 0


def _state(repo):
    return fold(EventLog(repo, "reader").read_all(), strict=False)


def _needs(repo) -> dict[str, list[str]]:
    return {i: list(it.needs) for i, it in _state(repo).items.items()}


def _doctor(repo) -> dict:
    return json.loads(run_cli(repo, "--json", "doctor", agent=AGENTS[0])[1])


def _ring(repo, n):
    _setup(repo, n)
    st = _state(repo)
    return [c.as_dict() for c in wait_cycles(st, Config.load(repo), time.time())]


def test_default_mode_is_propose():
    assert Config().schedule.auto_order == "propose"


def test_two_agent_cycle_resolves_in_apply_mode(repo):
    assert len(_ring(repo, 2)) == 1
    _mode(repo, "apply")
    out = _doctor(repo)
    assert len(out["auto_order"]["applied"]) == 1
    needs = _needs(repo)
    assert needs["T2"] == ["T1"] and not needs["T1"]  # the victim waits for the other
    assert find_cycles(_state(repo).items) == []


def test_three_agent_cycle_gets_one_edge_and_no_needs_cycle(repo):
    assert len(_ring(repo, 3)) == 1
    _mode(repo, "apply")
    out = _doctor(repo)
    assert len(out["auto_order"]["applied"]) == 1
    assert find_cycles(_state(repo).items) == []


def test_propose_mode_writes_nothing(repo):
    _ring(repo, 2)
    before = len(EventLog(repo, "reader").read_all())
    out = _doctor(repo)
    assert out["auto_order"]["mode"] == "propose" and len(out["auto_order"]["edges"]) == 1
    assert out["auto_order"]["applied"] == []
    assert all(not n for n in _needs(repo).values())
    assert len(EventLog(repo, "reader").read_all()) == before


def test_apply_is_idempotent_and_the_edge_is_marked_auto(repo):
    _ring(repo, 2)
    _mode(repo, "apply")
    _doctor(repo)
    n = len(EventLog(repo, "reader").read_all())
    again = _doctor(repo)
    assert again["auto_order"]["applied"] == []
    events = EventLog(repo, "reader").read_all()
    assert all(e.kind != "task.updated" for e in events[n:])
    (edge,) = AOS.auto_edges(events, _state(repo))
    assert edge["later"] == "T2" and edge["earlier"] == "T1" and "T2 now waits" in edge["reason"]


def test_off_mode_does_nothing(repo):
    _ring(repo, 2)
    _mode(repo, "off")
    out = _doctor(repo)
    assert out["auto_order"]["edges"] == [] and all(not n for n in _needs(repo).values())


def test_edge_is_reversible_by_update_needs(repo):
    _ring(repo, 2)
    _mode(repo, "apply")
    _doctor(repo)
    assert run_cli(repo, "update", "T2", "--needs", "", agent=AGENTS[1])[0] == 0
    assert _needs(repo)["T2"] == []
    assert AOS.auto_edges(EventLog(repo, "reader").read_all(), _state(repo)) == []


def _cycle(victim_item="T2"):
    link = lambda ag, it, bl: {"agent": ag, "item": it, "blocker": bl, "blockers": [bl]}  # noqa: E731
    return {
        "agents": ["a", "b"],
        "files": ["x"],
        "victim_item": victim_item,
        "links": [link("a", "T1", "T2"), link("b", "T2", "T1")],
    }


def test_settled_decide_and_opted_out_items_are_untouched():
    for bad in (
        AO.Cand("T1", state="done"),
        AO.Cand("T1", state="abandoned"),
        AO.Cand("T1", tags=("decide",)),
        AO.Cand("T1", tags=("no-auto-order",)),
    ):
        edges, notes = AO.propose([_cycle()], {"T1": bad, "T2": AO.Cand("T2")})
        assert edges == [] and notes, bad


def test_an_edge_that_would_close_a_needs_cycle_is_refused():
    items = {"T1": AO.Cand("T1", needs=("T2",)), "T2": AO.Cand("T2")}
    edges, _ = AO.propose([_cycle()], items)
    assert edges == []  # T2 -> T1 would close a cycle; T1 -> T2 is already there
    needs = {i: list(c.needs) for i, c in items.items()}
    for e in edges:
        needs[e.later].append(e.earlier)
    assert find_cycles(needs, lambda n: n) == []


def test_already_ordered_pair_proposes_nothing():
    items = {"T1": AO.Cand("T1"), "T2": AO.Cand("T2", needs=("T1",))}
    assert AO.propose([_cycle()], items) == ([], [])


def test_a_link_with_only_a_blockers_list_does_not_crash():
    cyc = {
        "agents": ["a"],
        "files": [],
        "victim_item": "T2",
        "links": [{"item": "T1", "blockers": ["T2"]}],
    }
    items = {"T1": AO.Cand("T1"), "T2": AO.Cand("T2")}
    (e,), _ = AO.propose([cyc], items)
    assert (e.later, e.earlier) == ("T2", "T1")


def test_a_link_blocked_by_two_leases_falls_back_to_the_eligible_one():
    cyc = {
        "agents": ["a", "b"],
        "files": [],
        "victim_item": "T9",
        "links": [{"item": "T1", "blocker": "T2", "blockers": ["T2", "T3"]}],
    }
    items = {"T1": AO.Cand("T1"), "T2": AO.Cand("T2", state="done"), "T3": AO.Cand("T3")}
    (e,), _ = AO.propose([cyc], items)
    assert (e.later, e.earlier) == ("T1", "T3")

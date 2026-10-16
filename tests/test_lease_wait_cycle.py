"""A cycle of agents waiting on each other's leases is found and reported (DX-lease-wait-cycle).

2026-10-15: three agents deadlocked 45+ minutes with every lease current and nothing said.
The cycle is structured data (`leasewait.find`) and `doctor` names it."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api.reporting.health import wait_cycles
from ddflow.config import Config
from ddflow.core import leasewait as LW
from ddflow.core.model import fold
from ddflow.infra.log import EventLog

AGENTS = ["agent-a", "agent-b", "agent-c"]


def _setup(repo, n: int) -> None:
    """n items T1..Tn, item i held by agent i on file f<i>.py; each then widens to f<i+1>."""
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


def _widen(repo, i: int, j: int) -> int:
    return run_cli(
        repo, "update", f"T{i}", "--globs", f"src/f{i}.py,src/f{j}.py", agent=AGENTS[i - 1]
    )[0]


def _cycles(repo):
    st = fold(EventLog(repo, "reader").read_all(), strict=False)
    return wait_cycles(st, Config.load(repo), time.time())


def test_two_agents_waiting_on_each_other_are_one_cycle(repo):
    _setup(repo, 2)
    assert _widen(repo, 1, 2) == 3 and _widen(repo, 2, 1) == 3
    (c,) = _cycles(repo)
    assert c.agents == ("agent-a", "agent-b")
    assert set(c.items) == {"T1", "T2"}
    assert set(c.files) == {"src/f1.py", "src/f2.py"}
    assert c.victim == "agent-b" and c.release == ("src/f2.py",)  # the later waiter yields
    assert "ddflow release T2" in c.remedy or "ddflow update T2" in c.remedy


def test_three_agents_in_a_ring_are_one_cycle(repo):
    _setup(repo, 3)
    assert _widen(repo, 1, 2) == _widen(repo, 2, 3) == _widen(repo, 3, 1) == 3
    (c,) = _cycles(repo)
    assert c.agents == tuple(AGENTS)
    assert {lk.holder for lk in c.links} == set(AGENTS)


def test_a_chain_that_does_not_close_is_not_a_cycle(repo):
    _setup(repo, 3)
    assert _widen(repo, 1, 2) == _widen(repo, 2, 3) == 3
    assert _cycles(repo) == []


def test_a_wait_on_ones_own_lease_is_a_cycle_of_one():
    w = LW.Wait("T1", "agent-a", 1.0, ("src/x.py",), (("T2", "agent-a"),))
    held = [LW.Held("T1", "agent-a", ("src/a.py",)), LW.Held("T2", "agent-a", ("src/x.py",))]
    (c,) = LW.find([w], held)
    assert c.agents == ("agent-a",) and c.victim == "agent-a"


def test_the_cycle_resolves_when_a_lease_is_released(repo):
    _setup(repo, 2)
    _widen(repo, 1, 2)
    _widen(repo, 2, 1)
    assert len(_cycles(repo)) == 1
    run_cli(repo, "release", "T2", agent=AGENTS[1])
    assert _cycles(repo) == []


def test_doctor_names_the_cycle_in_text_and_data(repo):
    _setup(repo, 2)
    _widen(repo, 1, 2)
    _widen(repo, 2, 1)
    code, out, _ = run_cli(repo, "doctor", agent=AGENTS[0])
    assert code == 1 and "lease deadlock: agent-a -> agent-b -> agent-a" in out
    _, js, _ = run_cli(repo, "--json", "doctor", agent=AGENTS[0])
    data = json.loads(js)["wait_cycles"]
    assert data[0]["agents"] == ["agent-a", "agent-b"] and data[0]["victim"] == "agent-b"


def test_the_knob_turns_the_report_off(repo):
    _setup(repo, 2)
    _widen(repo, 1, 2)
    _widen(repo, 2, 1)
    cfg = Config.load(repo)
    cfg.loops.wait_cycle = False
    st = fold(EventLog(repo, "reader").read_all(), strict=False)
    assert wait_cycles(st, cfg, time.time()) == []

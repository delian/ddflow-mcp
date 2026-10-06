"""plan() under the effective parallelism limit (B-af-wire-plan).

Throwaway projects, a scripted Decision: no host signal is assumed.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from conftest import run_cli

from ddflow.config import Config
from ddflow.core import flowcontrol as FC
from ddflow.core.model import fold
from ddflow.core.schedule import plan
from ddflow.infra.log import EventLog

NOW = time.time()


def _state(repo: Path, n: int, *, overlapping: bool = False, leased: int = 0):
    log = EventLog(repo, "a")
    for i in range(n):
        globs = ["shared.py"] if overlapping else [f"f{i}.py"]
        log.append("task.added", f"T{i}", {"title": "t", "kind": "task", "globs": globs})
    for i in range(leased):
        log.append("task.added", f"L{i}", {"title": "l", "kind": "task", "globs": [f"l{i}.py"]})
        log.append(
            "lease.acquired",
            f"L{i}",
            {"holder": f"agent{i}", "at": NOW, "ttl_s": 3600, "worktree": f"/w/{i}"},
        )
    return fold(log.read_all(), strict=False)


def _auto(limit: int, by: str = "load_per_core", paused: bool = False) -> FC.Decision:
    return FC.Decision(limit, "decrease", by, "test", paused)


def test_fixed_mode_is_byte_identical_to_no_decision(repo) -> None:
    st = _state(repo, 10)
    cfg = Config()
    old = plan(st, cfg, now=NOW)
    new = plan(st, cfg, now=NOW, parallel=FC.Decision(4, "fixed", "fixed", ""))
    assert [i.id for i in new.ready] == [i.id for i in old.ready]
    assert new.cap_note == old.cap_note == "the parallelism cap (schedule.max_parallel_tasks=4)"
    assert [(b.item, b.reason, b.detail) for b in new.blocked] == [
        (b.item, b.reason, b.detail) for b in old.blocked
    ]
    assert old.parallel_line == "" and new.parallel_line == "parallel: 4 (fixed)"


def test_auto_offers_up_to_the_limit_minus_in_flight(repo) -> None:
    st = _state(repo, 10, leased=2)
    p = plan(st, Config(), now=NOW, parallel=_auto(6))
    assert len(p.ready) == 4  # 6 minus the 2 in flight
    assert "limited by load per core" in p.cap_note
    assert "cap reached" not in p.cap_note
    assert p.parallel_line == "parallel: 6 (auto: ceiling 8; limited by load per core)"


def test_limited_by_independent_work(repo) -> None:
    st = _state(repo, 2)
    p = plan(st, Config(), now=NOW, parallel=_auto(6))
    assert len(p.ready) == 2
    assert p.parallel_line.endswith("limited by independent work)")


def test_overlapping_work_is_independent_work_too(repo) -> None:
    st = _state(repo, 5, overlapping=True)
    p = plan(st, Config(), now=NOW, parallel=_auto(6))
    assert len(p.ready) == 1
    assert "independent work" in p.parallel_line


def test_a_shrink_never_touches_a_running_lease(repo) -> None:
    st = _state(repo, 4, leased=5)
    before = {i: st.items[i].lease for i in st.items if st.items[i].lease}
    p = plan(st, Config(), now=NOW, parallel=_auto(3))
    assert p.ready == []
    assert len([b for b in p.blocked if b.item.startswith("L")]) == 5  # still held
    assert {i: st.items[i].lease for i in st.items if st.items[i].lease} == before
    held = [b for b in p.blocked if b.item.startswith("T")]
    assert held and all("cap reached" in b.detail for b in held)


def test_the_floor_is_one(repo) -> None:
    st = _state(repo, 3)
    p = plan(st, Config(), now=NOW, parallel=_auto(0))
    assert len(p.ready) == 1


def test_the_ceiling_is_named(repo) -> None:
    cfg = Config()
    cfg.schedule.max_parallel_max = 5
    st = _state(repo, 10)
    p = plan(st, cfg, now=NOW, parallel=_auto(5, by=FC.CEILING))
    assert len(p.ready) == 5
    assert p.parallel_line == "parallel: 5 (auto: ceiling 5; limited by ceiling)"


def test_admission_paused_offers_nothing(repo) -> None:
    st = _state(repo, 3)
    p = plan(st, Config(), now=NOW, parallel=_auto(4, by="disk_pressure", paused=True))
    assert p.ready == []
    assert "admission paused" in p.parallel_line and "paused" in p.cap_note


def test_a_zero_worktree_cap_follows_the_limit_and_a_number_still_caps(repo) -> None:
    st = _state(repo, 10)
    cfg = Config()
    assert len(plan(st, cfg, now=NOW, parallel=_auto(6)).ready) == 6
    cfg.worktree.max_parallel = 3
    p = plan(st, cfg, now=NOW, parallel=_auto(6))
    assert len(p.ready) == 3 and "worktree.max_parallel=3" in p.cap_note


def _ten(repo: Path) -> None:
    assert run_cli(repo, "init")[0] == 0
    for i in range(10):
        run_cli(repo, "task", "add", f"T{i}", "--globs", f"f{i}.py")


def test_status_and_brief_carry_the_line(repo) -> None:
    _ten(repo)
    _c, out, _e = run_cli(repo, "status")
    assert "parallel: 4 (auto: ceiling 8; limited by " in out, out
    _c, out, _e = run_cli(repo, "--json", "status")
    assert json.loads(out)["parallel"].startswith("parallel: 4 (auto")
    _c, out, _e = run_cli(repo, "brief")
    assert "parallel: 4 (auto" in out, out


def test_fixed_status_line(repo) -> None:
    _ten(repo)
    assert run_cli(repo, "config", "schedule.parallel", "fixed")[0] == 0
    _c, out, _e = run_cli(repo, "status")
    assert "parallel: 4 (fixed)" in out, out
    assert "held by the parallelism cap (schedule.max_parallel_tasks=4)" in out


def test_ddflow_status_carries_the_line(repo) -> None:
    from ddflow.surfaces.mcp import Server

    _ten(repo)
    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "ddflow_status", "arguments": {}},
    }
    r = Server(repo).handle(req)["result"]
    assert "parallel: 4 (auto" in json.dumps(r)


def test_exactly_filling_the_limit_with_nothing_held_is_independent_work(repo) -> None:
    st = _state(repo, 6)
    p = plan(st, Config(), now=NOW, parallel=_auto(6))
    assert len(p.ready) == 6 and p.capped == []
    assert p.parallel_line.endswith("limited by independent work)")


def test_an_underivable_limit_still_says_so(repo, monkeypatch) -> None:
    from ddflow.core.model import State
    from ddflow.services import flowstate as FL

    def boom(ctx, source=None, clock=None):
        raise RuntimeError("ring exploded")

    monkeypatch.setattr(FL, "current_limit", boom)
    d = FL.limit_for(repo, Config(), State())
    assert d.limit == 4 and "ring exploded" in d.reason
    p = plan(_state(repo, 2), Config(), now=NOW, parallel=d)
    assert p.parallel_line.startswith("parallel: 4 (auto")


def test_wait_plans_with_the_same_limit_as_next(repo, monkeypatch) -> None:
    from ddflow.api import lifecycle
    from ddflow.services import flowstate as FL

    _ten(repo)
    seen = []
    real = FL.limit_for

    def spy(repo_, cfg, st, events=()):
        seen.append(callable(events) or bool(events))
        return real(repo_, cfg, st, events)

    monkeypatch.setattr(FL, "limit_for", spy)
    lifecycle.next_(repo, agent="a1")
    n = len(seen)
    lifecycle.wait(repo, agent="a1", timeout_s=0)
    assert n >= 1 and len(seen) > n, seen  # wait asked for the limit too
    assert all(seen), seen  # and every caller handed over the log

"""The scheduler's reports agree with what it actually decided.

Three bugs, one theme -- a number or a sentence the scheduler hands a reader that is not
what it computed:

* B13ed484062: `critical_path(state, phase)` sliced the phase by direct parentage, so a
  chain of sub-tasks nested under a task vanished and the wall-clock floor read short.
* B40386f7a40: `plan` said "parallelism cap reached" for items ranked past the free
  slots while a slot was still free; an agent told to wait for that message to clear
  waited three hours on an item `claim` would have granted.
* Bdcce70d036: `status` counted blocked items only for reason "deps", so items held back
  by `max_parallel_tasks` were in no bucket and `tasks.total` did not add up.
"""

from __future__ import annotations

import time

from conftest import run_cli

from ddflow.core.model import fold
from ddflow.core.schedule import critical_path, plan


def test_the_critical_path_of_a_phase_walks_sub_tasks_nested_under_a_task(log):
    """B13ed484062: P > T > S1, S2 needs S1, S3 needs S2."""
    log.append("phase.added", "P", {"title": "phase"})
    log.append("task.added", "T", {"parent": "P", "globs": ["t"]})
    log.append("task.added", "S1", {"parent": "T", "globs": ["s1"]})
    log.append("task.added", "S2", {"parent": "T", "needs": ["S1"], "globs": ["s2"]})
    log.append("task.added", "S3", {"parent": "T", "needs": ["S2"], "globs": ["s3"]})
    st = fold(log.read_all())
    assert critical_path(st) == ["S1", "S2", "S3"]
    assert critical_path(st, "P") == ["S1", "S2", "S3"]
    assert critical_path(st, "T") == ["S1", "S2", "S3"]


def _queue(log, n: int):
    log.append("phase.added", "P", {"title": "phase"})
    for i in range(1, n + 1):
        log.append("task.added", f"U{i}", {"parent": "P", "globs": [f"u{i}"]})


def _hold(log, item: str, holder: str = "other") -> None:
    log.append("lease.acquired", item, {"holder": holder, "at": time.time(), "ttl_s": 600})
    log.append("item.started", item, {})


def test_a_free_slot_is_not_reported_as_the_cap_reached(log, cfg):
    """B40386f7a40: cap 2, one in flight -- one slot is FREE, and U2 takes it."""
    _queue(log, 4)
    _hold(log, "U1")
    cfg.schedule.max_parallel_tasks = 2
    cfg.worktree.max_parallel = 8
    p = plan(fold(log.read_all()), cfg, agent="me")
    assert [i.id for i in p.ready] == ["U2"]
    held = {b.item: b.detail for b in p.blocked}
    assert set(held) == {"U3", "U4"}
    for detail in held.values():
        assert "cap reached" not in detail, detail
        assert "1 slot(s) free" in detail and "U2" in detail, detail
    assert p.capped == ["U3", "U4"]


def test_a_full_cap_still_says_cap_reached(log, cfg):
    """The wording `wait` keys on (`_blocking_leases`) when NO slot is free."""
    _queue(log, 3)
    _hold(log, "U1")
    _hold(log, "U2", "third")
    cfg.schedule.max_parallel_tasks = 2
    p = plan(fold(log.read_all()), cfg, agent="me")
    assert not p.ready and p.capped == ["U3"]
    assert "cap reached" in p.blocked[0].detail
    assert "1 held by" in p.summary() and "0 blocked" in p.summary(), p.summary()


def test_a_cap_lowered_below_what_is_in_flight_holds_everything(log, cfg):
    """Reviewer probe: three in flight, cap lowered to one. No slot is free, so nothing
    is offered and every ready item is held -- never a negative count of free slots."""
    _queue(log, 5)
    for i, who in ((1, "a"), (2, "b"), (3, "c")):
        _hold(log, f"U{i}", who)
    cfg.schedule.max_parallel_tasks = 1
    p = plan(fold(log.read_all()), cfg, agent="me")
    assert not p.ready and p.capped == ["U4", "U5"]
    assert all("cap reached" in b.detail and "free" not in b.detail for b in p.blocked)


def test_a_cap_held_item_waits_on_every_item_in_flight(log, cfg):
    """Any release frees a slot. Named in `waiting_on`, so an any-wait registers against
    every holder whether or not the wording says "cap reached" (critic finding)."""
    _queue(log, 4)
    _hold(log, "U1", "x")
    _hold(log, "U2", "y")
    cfg.schedule.max_parallel_tasks = 3
    p = plan(fold(log.read_all()), cfg, agent="me")
    assert [i.id for i in p.ready] == ["U3"] and p.capped == ["U4"]
    assert p.blocked[0].waiting_on == ["U1", "U2"]


def test_a_free_slot_any_wait_still_waits_on_every_holder(repo):
    """The case the wording change could have narrowed: under deps_only the one offered
    item conflicts with X's lease, so the any-wait falls through to the blockers -- and a
    release by Y frees the slot the held item needs, so Y must be waited on too."""
    from ddflow.api import lifecycle as LA

    run_cli(repo, "init")
    cfg = repo / ".ddflow" / "config.toml"
    text = cfg.read_text().replace("max_parallel_tasks = 4", "max_parallel_tasks = 3")
    cfg.write_text(text.replace("[schedule]", '[schedule]\nready_policy = "deps_only"', 1))
    run_cli(repo, "task", "add", "A", "--globs", "a.py")
    run_cli(repo, "task", "add", "D", "--globs", "d.py")
    run_cli(repo, "task", "add", "B", "--globs", "a.py")
    run_cli(repo, "task", "add", "C", "--globs", "c.py")
    assert LA.claim(repo, "A", no_worktree=True, agent="x").ok
    assert LA.claim(repo, "D", no_worktree=True, agent="y").ok
    out = LA.wait(repo, timeout_s=0, agent="w")
    held = [b for b in out.data["blocked"] if b["item"] == "C"]
    assert held and "free" in held[0]["detail"], out.data["blocked"]
    assert out.data["waiting_on"] == ["A", "D"], out.data


def _buckets(t: dict) -> int:
    return sum(t[k] for k in ("done", "running", "ready", "held_by_cap", "blocked", "review"))


def test_status_counts_every_task_in_exactly_one_bucket(repo):
    """Bdcce70d036: total == done + running + ready + held_by_cap + blocked (+ review,
    abandoned), before and after a claim, with the cap holding items back."""
    from ddflow import api

    run_cli(repo, "init")
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text().replace("max_parallel_tasks = 4", "max_parallel_tasks = 2"))
    run_cli(repo, "phase", "add", "P", "--title", "P")
    for i in range(1, 6):
        run_cli(repo, "task", "add", f"U{i}", "--phase", "P", "--globs", f"u{i}")
    run_cli(repo, "task", "add", "D1", "--phase", "P", "--globs", "d1", "--needs", "U1")
    run_cli(repo, "task", "add", "X1", "--phase", "P", "--globs", "x1")
    run_cli(repo, "abandon", "X1", "--reason", "not needed")

    t = api.status(repo).data["tasks"]
    assert t["total"] == 7 and t["abandoned"] == 1, t
    assert (t["ready"], t["held_by_cap"], t["blocked"]) == (2, 3, 1), t
    assert _buckets(t) + t["abandoned"] == t["total"], t

    assert run_cli(repo, "claim", "U1", "--no-worktree", agent="a1")[0] == 0
    out = api.status(repo)
    t = out.data["tasks"]
    assert (t["running"], t["ready"], t["held_by_cap"], t["blocked"]) == (1, 1, 3, 1), t
    assert _buckets(t) + t["abandoned"] == t["total"], t
    assert [x["id"] for x in out.data["held_by_cap"]] == ["U3", "U4", "U5"]


def test_status_and_brief_prose_name_the_items_the_cap_holds(repo):
    """Bdcce70d036, the reader's side: 'Ready now: 2' with the rest hidden made an
    operator conclude only two items were startable."""
    run_cli(repo, "init")
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text().replace("max_parallel_tasks = 4", "max_parallel_tasks = 2"))
    for i in range(1, 5):
        run_cli(repo, "task", "add", f"U{i}", "--globs", f"u{i}")
    _code, status, _err = run_cli(repo, "status")
    assert "max_parallel_tasks=2" in status and "U3, U4" in status, status
    assert "2 more are ready but held by" in status, status
    _code, brief, _err = run_cli(repo, "brief")
    assert "2 more are ready but held" in brief and "`U3`, `U4`" in brief, brief


def test_with_nothing_else_ready_the_cap_line_does_not_say_more(repo):
    """Critic finding on B-fix-schedule-cap-reporting: "_Nothing ready._" followed by
    "N more are ready" read as a contradiction."""
    run_cli(repo, "init")
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text().replace("max_parallel_tasks = 4", "max_parallel_tasks = 1"))
    for i in range(1, 4):
        run_cli(repo, "task", "add", f"U{i}", "--globs", f"u{i}")
    assert run_cli(repo, "claim", "U1", "--no-worktree", agent="a1")[0] == 0
    _code, status, _err = run_cli(repo, "status")
    assert "2 are ready but held by" in status and "more" not in status, status
    _code, brief, _err = run_cli(repo, "brief")
    assert "2 are ready but held by" in brief, brief

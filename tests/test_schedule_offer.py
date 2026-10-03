"""plan() offers conflict-aware: two offered items never overlap each other.

Before, `ready[:slots]` was cut in priority order, so two offered items could overlap
and the second claim was refused. Now an item that overlaps one already offered goes to
`blocked` (reason "conflict") and the next independent item takes its slot.
"""

from __future__ import annotations

import time

from ddflow.core.model import fold
from ddflow.core.schedule import plan


def _task(log, tid: str, prio: int, globs: list[str]) -> None:
    log.append("task.added", tid, {"parent": "P", "priority": prio, "globs": globs})


def _queue(log, spec: list[tuple[str, int, list[str]]]) -> None:
    log.append("phase.added", "P", {"title": "phase"})
    for tid, prio, globs in spec:
        _task(log, tid, prio, globs)


def _plan(log, cfg, slots: int):
    cfg.schedule.max_parallel_tasks = slots
    cfg.worktree.max_parallel = 99
    return plan(fold(log.read_all()), cfg, agent="me")


def test_an_overlapping_item_yields_its_slot_to_an_independent_one(log, cfg):
    _queue(log, [("A", 1, ["a.py"]), ("B", 2, ["a.py"]), ("C", 3, ["c.py"])])
    p = _plan(log, cfg, 2)
    assert [i.id for i in p.ready] == ["A", "C"]
    blocked = {b.item: b for b in p.blocked}
    assert blocked["B"].reason == "conflict"
    assert "globs overlap A" in blocked["B"].detail
    assert "offered in this plan" in blocked["B"].detail
    assert blocked["B"].waiting_on == ["A"]
    assert p.capped == []


def test_independent_items_are_offered_exactly_as_before(log, cfg):
    _queue(log, [("A", 1, ["a.py"]), ("B", 2, ["b.py"]), ("C", 3, ["c.py"])])
    p = _plan(log, cfg, 2)
    assert [i.id for i in p.ready] == ["A", "B"]
    assert p.capped == ["C"]
    assert [b.item for b in p.blocked] == ["C"]
    assert p.blocked[0].reason == "state"
    assert "held by the parallelism cap" in p.blocked[0].detail


def test_a_live_lease_still_blocks_an_overlapping_item(log, cfg):
    _queue(log, [("A", 1, ["a.py"]), ("B", 2, ["b.py"])])
    log.append(
        "lease.acquired",
        "A",
        {"holder": "other", "at": time.time(), "ttl_s": 600, "globs": ["a.py"]},
    )
    log.append("item.started", "A", {})
    _task(log, "D", 3, ["a.py"])
    p = _plan(log, cfg, 4)
    assert [i.id for i in p.ready] == ["B"]
    d = {b.item: b for b in p.blocked}["D"]
    assert d.reason == "conflict" and "held by other" in d.detail


def test_spare_slots_do_not_offer_overlapping_items_and_are_not_cap_held(log, cfg):
    _queue(log, [("A", 1, ["a.py"]), ("B", 2, ["a.py"]), ("C", 3, ["a.py"])])
    p = _plan(log, cfg, 4)
    assert [i.id for i in p.ready] == ["A"]
    assert p.capped == []
    assert {b.item for b in p.blocked if b.reason == "conflict"} == {"B", "C"}
    s = p.summary()
    assert "2 overlap an offered item" in s
    assert "held by" not in s
    assert "0 blocked" in s


def test_shared_globs_overlap_nothing(log, cfg):
    cfg.lease.shared_globs = ["CHANGELOG.md"]
    _queue(log, [("A", 1, ["a.py", "CHANGELOG.md"]), ("B", 2, ["b.py", "CHANGELOG.md"])])
    p = _plan(log, cfg, 2)
    assert [i.id for i in p.ready] == ["A", "B"]
    assert not p.blocked


def test_a_held_item_is_not_offered_and_does_not_shadow_the_items_it_overlaps(log, cfg):
    """`hold` (a waiter's reservation) is applied before the overlap check: B overlaps A, A
    is withheld, so B is offered rather than blocked behind an item nobody is offered."""
    from ddflow.core.schedule import Blocked

    _queue(log, [("A", 1, ["a.py"]), ("B", 2, ["a.py"]), ("C", 3, ["c.py"])])
    cfg.schedule.max_parallel_tasks = 2
    cfg.worktree.max_parallel = 99
    p = plan(
        fold(log.read_all()),
        cfg,
        agent="me",
        hold=lambda it: Blocked(it.id, "conflict", "reserved", []) if it.id == "A" else None,
    )
    assert [i.id for i in p.ready] == ["B", "C"]
    assert [b.item for b in p.blocked] == ["A"]

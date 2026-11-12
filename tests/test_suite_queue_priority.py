"""The suite lock queue: priority classes, cost-aware order with aging, per-agent fair share and
an estimated wait (SQ-priority-fair-queue)."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from ddflow.config import Config
from ddflow.infra.signals import FakeSource
from ddflow.services import suitelock as SL
from ddflow.services import suitequeue as SQ


def W(name, klass="item", cost=60.0, agent="a", waited=0.0):
    return SQ.Waiting(name, klass, cost, agent, waited)


def order(waiting, *, factor=3.0, share=1.0, default=600.0):
    return SQ.order(waiting, max_wait_factor=factor, agent_share=share, default_cost_s=default)


def names(waiting, **kw):
    return [w.name for w in order(waiting, **kw)]


def test_a_red_main_fix_overtakes_older_item_runs():
    queue = [
        W("item1", cost=600, waited=300),
        W("item2", cost=600, waited=200),
        W("fix", "red_main", cost=600, waited=1),
    ]
    assert names(queue)[0] == "fix"


def test_the_classes_rank_red_main_integrator_phase_item_baseline():
    queue = [
        W("base", "baseline", waited=50),
        W("item", "item", waited=40),
        W("phase", "phase", waited=30),
        W("integ", "integrator", waited=20),
        W("fix", "red_main", waited=10),
    ]
    assert names(queue) == ["fix", "integ", "phase", "item", "base"]


def test_within_a_class_the_shortest_predicted_run_goes_first():
    queue = [W("long", cost=1800, waited=5), W("short", cost=30, waited=4), W("mid", cost=300)]
    assert names(queue) == ["short", "mid", "long"]


def test_a_long_run_is_not_starved_past_the_aging_bound():
    """Short runs keep arriving ahead of it, but once it has waited factor x its own cost it goes
    first, whatever the class of the others."""
    long_run = W("long", cost=100, waited=299)  # 2.99 x its cost: not yet
    queue = [long_run, W("fix", "red_main", waited=1), W("s", cost=1)]
    assert names(queue, factor=3.0)[0] == "fix"
    aged = W("long", cost=100, waited=301)
    assert names([aged, W("fix", "red_main", waited=1), W("s", cost=1)], factor=3.0)[0] == "long"


def test_an_unknown_cost_ages_by_the_default_cost():
    old = W("old", cost=0.0, waited=2000)  # default 600 x 3 = 1800: overdue
    assert names([W("fix", "red_main"), old])[0] == "old"


def test_one_agent_flooding_the_queue_cannot_take_all_the_slots():
    flood = [W(f"x{i}", agent="x", waited=100 - i) for i in range(5)]
    queue = [*flood, W("y1", agent="y", waited=1), W("z1", agent="z", waited=1)]
    got = names(queue, share=0.5)  # 7 waiting: x may lead 4 (ceil 3.5), the rest queue behind
    assert got.index("y1") < got.index("x4") and got.index("z1") < got.index("x4")
    assert got[:2] == ["x0", "x1"]  # the cap bounds, it does not punish the first ones


def test_a_lone_agent_is_never_held_back_by_its_own_share():
    queue = [W(f"x{i}", agent="x", waited=10 - i) for i in range(4)]
    assert names(queue, share=0.25) == [f"x{i}" for i in range(4)]


def test_the_estimate_counts_the_cost_ahead_over_the_slots():
    waiting = [W("a", cost=600), W("b", cost=300), W("me", cost=60)]
    ahead = SQ.estimate(waiting, "me", free_in_s=[0.0])
    assert ahead == {"position": 3, "ahead": 2, "wait_s": 900.0, "predicted_s": 60.0}
    two = SQ.estimate(waiting, "me", free_in_s=[0.0, 0.0])
    assert two["wait_s"] == 300.0  # the shorter of the two ahead frees a slot first
    busy = SQ.estimate(waiting, "me", free_in_s=[120.0])
    assert busy["wait_s"] == 1020.0  # the running suite has 120 s left, then both ahead
    unknown = [W("a", cost=0.0), W("me", cost=0.0)]
    assert SQ.estimate(unknown, "me", free_in_s=[0.0], default_cost_s=60.0)["wait_s"] == 60.0


def test_the_estimate_is_said_in_the_busy_line():
    line = SQ.say_estimate({"position": 4, "ahead": 3, "wait_s": 840.0, "predicted_s": 1860.0})
    assert line == "estimated wait 14m (3 ahead, predicted 31m)"


def test_the_median_of_measured_runs_predicts_the_cost(tmp_path: Path):
    for s in (100.0, 900.0, 300.0):
        SQ.record_cost(tmp_path, "ci:full", s, keep=20)
    assert SQ.predicted_cost(tmp_path, "ci:full", keep=20) == 300.0
    assert SQ.predicted_cost(tmp_path, "unseen", keep=20) == 0.0


def test_the_cost_window_keeps_the_newest_runs(tmp_path: Path):
    for s in range(1, 8):
        SQ.record_cost(tmp_path, "k", float(s), keep=3)
    assert SQ.predicted_cost(tmp_path, "k", keep=3) == 6.0  # median of 5, 6, 7


def test_the_class_comes_from_the_holder_label_by_the_configured_patterns():
    pats = Config().suites.class_patterns
    assert SQ.classify("fix-Bmain-red-x of repo", pats) == "red_main"
    assert SQ.classify("queue run of repo", pats) == "integrator"
    assert SQ.classify("agent-7", pats) == "item"


def cfg(wait=10.0, **suites):
    base = {
        "max_concurrent": 1,
        "cores_per_suite": 96,
        "load_ceiling_per_core": 1e9,
        "load_poll_s": 0.0,
        "max_wait_factor": 3.0,
        "agent_share": 1.0,
        "default_cost_s": 600.0,
        "cost_runs": 20,
        "class_patterns": Config().suites.class_patterns,
    }
    base.update(suites)
    return NS(
        ci=NS(
            max_concurrent_suites=1,
            suite_wait_s=wait,
            suite_queue_stale_s=120,
            suite_queue_grace_s=120,
        ),
        suites=NS(**base),
    )


def _until(pred, timeout=10.0):
    end = time.monotonic() + timeout
    while not pred():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.02)


def _waiter(d, name, order_list, **kw):
    def go():
        with SL.hold(cfg(30.0), name, d, say=lambda _t: None, source=FakeSource([{}]), **kw):
            order_list.append(name)

    t = threading.Thread(target=go, daemon=True)
    t.start()
    return t


def test_a_queued_fix_of_red_main_overtakes_older_item_runs_through_the_lock(tmp_path: Path):
    got: list[str] = []
    tickets = lambda: len(list((tmp_path / "queue").glob("*.ticket")))  # noqa: E731
    with SL.hold(cfg(), "holder", tmp_path, say=lambda _t: None):
        threads = [_waiter(tmp_path, "item-1", got), None, None]
        _until(lambda: tickets() == 1)
        threads[1] = _waiter(tmp_path, "item-2", got)
        _until(lambda: tickets() == 2)
        threads[2] = _waiter(tmp_path, "fix-Bmain-red-boom", got, klass="red_main")
        _until(lambda: tickets() == 3)
    for t in threads:
        t.join(30)
    assert got == [
        fix_name(),
        "item-1",
        "item-2",
    ]  # each ran once, the fix first, the items in arrival order


def fix_name():
    return "fix-Bmain-red-boom"


def test_the_busy_line_carries_the_estimate(tmp_path: Path):
    said: list[str] = []
    with SL.hold(cfg(), "holder", tmp_path, say=lambda _t: None):
        with pytest.raises(SL.SuiteBusy):
            with SL.hold(cfg(wait=0.4), "late", tmp_path, say=said.append, source=FakeSource([{}])):
                pass
    assert any("estimated wait" in line and "ahead" in line for line in said), said


def test_a_finished_run_teaches_the_cost(tmp_path: Path):
    with SL.hold(cfg(), "holder", tmp_path, say=lambda _t: None, scope="ci:full"):
        time.sleep(0.05)
    assert 0.0 < SQ.predicted_cost(tmp_path, "ci:full", keep=20) < 5.0


def test_the_new_knobs_exist():
    s = Config().suites
    assert s.max_wait_factor > 0 and 0 < s.agent_share <= 1 and s.default_cost_s > 0
    assert s.cost_runs >= 1 and "red_main" in s.class_patterns


def test_an_overdue_run_is_not_deferred_by_the_fair_share():
    a1 = W("A1", cost=10, agent="a", waited=100)
    a2 = W("A2", cost=10, agent="a", waited=90)
    others = [W(f"o{i}", cost=1000, agent=f"o{i}", waited=0) for i in range(3)]
    got = names([a1, a2, *others], factor=1.0, share=0.2, default=10.0)
    assert got[:2] == ["A1", "A2"]


def test_a_malformed_or_costless_holder_note_does_not_break_the_estimate(tmp_path: Path):
    import json as _json

    slots = tmp_path
    (slots / "suites.0.lock").touch()
    from ddflow.infra import fsio

    with fsio.file_lock(slots / "suites.0.lock", 0):
        for body in (
            "{}",
            "[]",
            "null",
            '"x"',
            '{"cost_s": 100}',
            "not json",
            _json.dumps({"at": time.time() - 1}),
        ):
            (slots / "suites.0.holder").write_text(body)
            line = [W("me", cost=60.0)]
            assert "estimated wait" in SL._eta(line, "me", 1, slots, 600.0)


def test_two_tickets_of_one_holder_without_notes_are_one_agent(tmp_path: Path):
    q = tmp_path / "queue"
    q.mkdir()
    for ts in ("00000000000000000001", "00000000000000000002"):
        (q / f"{ts}-same.ticket").touch()
    rows = SL._ordered(cfg(), q, sorted(p.name for p in q.glob("*.ticket")))
    assert len({w.agent for w in rows}) == 1


def test_an_overdue_run_counts_against_its_agents_share():
    a1 = W("a1", cost=10, agent="a", waited=10)  # overdue
    a2 = W("a2", cost=1, agent="a", waited=0)
    b = W("b", cost=1, agent="b", waited=0)
    c = W("c", cost=1, agent="c", waited=0)
    got = names([a1, a2, b, c], factor=1.0, share=0.2, default=10.0)
    assert got[0] == "a1" and got.index("a2") > got.index("b") and got.index("a2") > got.index("c")


def test_a_costless_holder_that_has_run_long_is_not_about_to_finish(tmp_path: Path):
    import json as _json

    from ddflow.infra import fsio

    (tmp_path / "suites.0.lock").touch()
    with fsio.file_lock(tmp_path / "suites.0.lock", 0):
        (tmp_path / "suites.0.holder").write_text(_json.dumps({"at": time.time() - 7200}))
        line = [W("me", cost=60.0)]
        assert "estimated wait 10m" in SL._eta(line, "me", 1, tmp_path, 600.0)


def test_a_block_can_queue_its_holds_as_a_class(tmp_path: Path):
    with SL.suite_class("integrator"):
        with SL.hold(cfg(), "agent-x", tmp_path, say=lambda _t: None):
            pass
    assert SL._CLASS.get() is None  # restored after the block
    q = tmp_path / "queue"
    got: list[str] = []
    with SL.hold(cfg(), "holder", tmp_path, say=lambda _t: None):
        with SL.suite_class("integrator"):
            import contextvars

            def go():
                with SL.hold(cfg(30.0), "agent-y", tmp_path, say=lambda _t: None):
                    got.append("agent-y")

            t = threading.Thread(target=contextvars.copy_context().run, args=(go,), daemon=True)
            t.start()
            _until(lambda: len(list(q.glob("*.ticket"))) == 1)
        note = SQ.read_note(next(q.glob("*.ticket")))
    t.join(10)
    assert got == ["agent-y"]  # the queued hold really ran
    assert note.get("class") == "integrator"

"""`[schedule].reserved_slots`: slots only bug fixes and `reserved_tags` tasks may take.

Operator 2026-10-15: 'Add one additional slot for the bugs and the new features'. Raising
`max_parallel_tasks` was the only lever, and any ready item could then take the slot, so a
bug filed while features filled the queue waited behind all of them.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from conftest import run_cli

from ddflow.adapters.fakes.optimizer import FakeOptimizer
from ddflow.adapters.optimizer import Candidate, GreedyV1, Slots
from ddflow.config import Config
from ddflow.core import flowcontrol as FC
from ddflow.core.model import fold
from ddflow.core.schedule import (
    idle_reserved,
    ordinary_free,
    plan,
    reserved_in_force,
    reserved_items,
)
from ddflow.infra import signals as SIG
from ddflow.services import flowstate as F

OK = 0


def _task(log, id_: str, *tags: str, priority: int = 100) -> None:
    log.append(
        "task.added",
        id_,
        {"parent": "P", "globs": [id_.lower()], "tags": list(tags), "priority": priority},
    )


def _queue(log, *ids: str) -> None:
    log.append("phase.added", "P", {"title": "phase"})
    for i in ids:
        _task(log, i)


def _hold(log, item: str, holder: str = "other", worktree: str = "") -> None:
    log.append(
        "lease.acquired",
        item,
        {"holder": holder, "at": time.time(), "ttl_s": 600, "worktree": worktree},
    )
    log.append("item.started", item, {})


def _ready(p) -> list[str]:
    return [i.id for i in p.ready]


def _reserve(cfg, cap: int = 3, reserved: int = 1) -> None:
    cfg.schedule.max_parallel_tasks = cap
    cfg.schedule.reserved_slots = reserved


def test_two_features_run_the_third_is_held_and_a_bug_fix_still_starts(log, cfg):
    """The item's own acceptance: cap 3, one reserved."""
    _queue(log, "F1", "F2", "F3")
    _task(log, "fix-B", "bug")
    _hold(log, "F1")
    _hold(log, "F2", "third")
    _reserve(cfg)
    p = plan(fold(log.read_all()), cfg, agent="me")
    assert _ready(p) == ["fix-B"]
    assert p.capped == ["F3"]


def test_a_reserved_slot_with_no_such_work_stays_free(log, cfg):
    _queue(log, "F1", "F2", "F3", "F4")
    _hold(log, "F1")
    _hold(log, "F2", "third")
    _reserve(cfg)
    p = plan(fold(log.read_all()), cfg, agent="me")
    assert _ready(p) == []
    assert p.capped == ["F3", "F4"]


def test_a_fresh_queue_offers_features_only_up_to_the_ordinary_share(log, cfg):
    _queue(log, "F1", "F2", "F3")
    _reserve(cfg)
    p = plan(fold(log.read_all()), cfg, agent="me")
    assert _ready(p) == ["F1", "F2"] and p.capped == ["F3"]


def test_a_cap_hold_says_a_named_claim_is_not_capped(log, cfg):
    """B30f9d383ba: a cap-held item whose free slot went to a HIGHER-RANKED item (which may
    have no lease) is listed as blocked while `claim <id>` never applies the cap to a NAMED
    item; the detail must say a named claim grants it -- otherwise a lane cannot tell a wait
    for a slot from an item it could claim right now."""
    _queue(log, "F1", "F2", "F3", "F4")
    _hold(log, "F1")
    cfg.schedule.max_parallel_tasks = 2
    cfg.schedule.reserved_slots = 0
    p = plan(fold(log.read_all()), cfg, agent="me")
    goods = [b for b in p.blocked if b.item in ("F3", "F4")]
    assert goods and p.capped == ["F3", "F4"]
    why = goods[0].detail
    # the free slot went to higher-ranked F2 (not "cap reached"): the branch the bug is about
    assert "slot(s) free, offered to higher-ranked F2" in why, why
    assert "not capped" in why and "grants it now" in why, why


@pytest.mark.parametrize("tag", ["bug", "bugfix", "fix", "hotfix", "ddflow-gap"])
def test_each_reserved_kind_of_tag_may_use_the_slot(log, cfg, tag):
    _queue(log, "F1", "F2")
    _task(log, "T", tag)
    _hold(log, "F1")
    _hold(log, "F2", "third")
    _reserve(cfg)
    assert _ready(plan(fold(log.read_all()), cfg, agent="me")) == ["T"]


def test_an_item_an_open_bug_names_may_use_the_slot(log, cfg):
    _queue(log, "F1", "F2", "F3")
    log.append("bug.found", "B1", {"summary": "wrong", "item": "F3"})
    _hold(log, "F1")
    _hold(log, "F2", "third")
    _reserve(cfg)
    assert _ready(plan(fold(log.read_all()), cfg, agent="me")) == ["F3"]


def test_an_unconfirmed_bug_earns_its_item_no_reserved_slot(log, cfg):
    """`bug_items` is the one definition: a repro that ran and did not fail counts for
    nothing, here as in the offer order."""
    _queue(log, "F1", "F2", "F3")
    log.append("bug.found", "B1", {"summary": "wrong", "item": "F3"})
    _hold(log, "F1")
    _hold(log, "F2", "third")
    _reserve(cfg)
    st = fold(log.read_all())
    st.bugs["B1"].reproduced = "no"
    p = plan(st, cfg, agent="me")
    assert _ready(p) == [] and p.capped == ["F3"]


def test_reserved_tags_is_a_knob(log, cfg):
    _queue(log, "F1", "F2")
    _task(log, "G", "ddflow-gap")
    _task(log, "V", "vip")
    _hold(log, "F1")
    _hold(log, "F2", "third")
    _reserve(cfg)
    cfg.schedule.reserved_tags = ["vip"]
    assert _ready(plan(fold(log.read_all()), cfg, agent="me")) == ["V"]


def test_a_bug_fix_is_reserved_work_with_bugs_first_off(log, cfg):
    """`bugs_first` is the ORDER; what may use the reserved slot does not hang on it."""
    _queue(log, "F1", "F2")
    _task(log, "fix-B", "bug")
    _hold(log, "F1")
    _hold(log, "F2", "third")
    _reserve(cfg)
    cfg.schedule.bugs_first = False
    assert _ready(plan(fold(log.read_all()), cfg, agent="me")) == ["fix-B"]


def test_a_bug_fix_in_flight_fills_the_reserved_slot_first(log, cfg):
    """One reserved, one bug running: the reservation is in use, so the two ordinary slots
    are still open. Counting the bug against them would idle a slot for nothing."""
    _queue(log, "F1", "F2", "F3")
    _task(log, "fix-B", "bug")
    _hold(log, "fix-B")
    _reserve(cfg)
    p = plan(fold(log.read_all()), cfg, agent="me")
    assert _ready(p) == ["F1", "F2"] and p.capped == ["F3"]


def test_a_second_bug_fix_overflows_into_an_ordinary_slot(log, cfg):
    """Reserved work may use every free slot; the reservation only keeps features out."""
    _queue(log, "F1")
    _task(log, "fix-A", "bug")
    _task(log, "fix-B", "bug")
    _task(log, "fix-C", "bug")
    _reserve(cfg)
    p = plan(fold(log.read_all()), cfg, agent="me")
    assert _ready(p) == ["fix-A", "fix-B", "fix-C"] and p.capped == ["F1"]


def test_the_offer_order_survives_a_reservation(log, cfg):
    """One walk in offer order: a tagged item at a low priority does not jump a feature."""
    log.append("phase.added", "P", {"title": "phase"})
    _task(log, "F1", priority=1)
    _task(log, "G", "ddflow-gap", priority=50)
    _task(log, "F2", priority=60)
    _task(log, "F3", priority=70)
    _reserve(cfg)
    p = plan(fold(log.read_all()), cfg, agent="me")
    assert _ready(p) == ["F1", "G", "F2"] and p.capped == ["F3"]


def test_a_reserved_item_a_feature_overlaps_is_still_a_conflict(log, cfg):
    log.append("phase.added", "P", {"title": "phase"})
    log.append("task.added", "fix-B", {"parent": "P", "globs": ["a"], "tags": ["bug"]})
    log.append("task.added", "F1", {"parent": "P", "globs": ["a"]})
    log.append("task.added", "F2", {"parent": "P", "globs": ["b"]})
    _reserve(cfg)
    p = plan(fold(log.read_all()), cfg, agent="me")
    assert _ready(p) == ["fix-B", "F2"] and p.overlapped == ["F1"]


def test_zero_reserved_slots_is_exactly_the_old_offer(log, cfg):
    _queue(log, "F1", "F2", "F3", "F4")
    _hold(log, "F1")
    cfg.schedule.max_parallel_tasks = 3
    assert cfg.schedule.reserved_slots == 0
    p = plan(fold(log.read_all()), cfg, agent="me")
    assert _ready(p) == ["F2", "F3"] and p.capped == ["F4"]
    assert p.reserved_note == "" and "reserved" not in p.summary()


@pytest.mark.parametrize(("cap", "wanted", "force"), [(1, 1, 0), (2, 5, 1), (3, 1, 1), (3, 0, 0)])
def test_one_slot_always_stays_open_to_ordinary_work(cap, wanted, force):
    """A reservation covering the whole limit would hold every feature for good, and the
    adaptive limit can drop that low on its own."""
    assert reserved_in_force(wanted, cap) == force


def test_a_reservation_as_large_as_the_limit_does_not_strand_features(log, cfg):
    _queue(log, "F1", "F2")
    _reserve(cfg, cap=1, reserved=1)
    p = plan(fold(log.read_all()), cfg, agent="me")
    assert _ready(p) == ["F1"]
    assert "0 of 1" in p.reserved_note and "1 set" in p.reserved_note, p.reserved_note


def test_ordinary_free_is_never_negative():
    assert ordinary_free(3, 1, 9) == 0
    assert ordinary_free(3, 1, 1) == 1


def test_the_reservation_follows_the_limit_in_force(log, cfg):
    """The adaptive limit, not the start value: limit 2 leaves ONE ordinary slot."""
    _queue(log, "F1", "F2", "F3")
    _reserve(cfg, cap=4)
    decision = FC.Decision(2, "auto", "load_per_core", "", False)
    p = plan(fold(log.read_all()), cfg, agent="me", parallel=decision)
    assert _ready(p) == ["F1"]
    assert "1 of 2" in p.reserved_note, p.reserved_note


def test_paused_admission_offers_nothing_even_to_a_bug_fix(log, cfg):
    """A pause is a machine under pressure: a reservation is a share, not an exemption."""
    _queue(log, "F1")
    _task(log, "fix-B", "bug")
    _reserve(cfg)
    decision = FC.Decision(3, "auto", "memory_pressure", "memory", True)
    assert _ready(plan(fold(log.read_all()), cfg, agent="me", parallel=decision)) == []


def test_the_worktree_cap_keeps_a_tree_for_the_reserved_slot(log, cfg):
    """A flight slot with no tree to work in is no slot: with worktree.max_parallel the
    tighter cap, features must not take the last tree either."""
    _queue(log, "F1", "F2")
    _task(log, "fix-B", "bug")
    _hold(log, "F1", worktree="/t/f1")
    _reserve(cfg, cap=8)
    cfg.worktree.max_parallel = 2
    p = plan(fold(log.read_all()), cfg, agent="me")
    assert _ready(p) == ["fix-B"] and p.capped == ["F2"]


def test_a_full_worktree_cap_is_named_with_its_number(log, cfg):
    _queue(log, "F1", "F2", "F3")
    _hold(log, "F1", "x", worktree="/t/f1")
    _hold(log, "F2", "y", worktree="/t/f2")
    _reserve(cfg, cap=8)
    cfg.worktree.max_parallel = 2
    p = plan(fold(log.read_all()), cfg, agent="me")
    assert p.cap_note == "the worktree cap (worktree.max_parallel=2)"
    assert "worktree cap reached (worktree.max_parallel=2)" in p.blocked[-1].detail


def test_a_worktree_lease_counts_against_the_limit_like_any_other(log, cfg):
    """Every live lease is in flight, worktree or not (`plan`'s two-caps comment), so an
    ordinary worktree lease uses the ordinary share: reading it as outside the limit would
    offer a third item to a cap of two."""
    _queue(log, "F1", "F2")
    _hold(log, "F1", worktree="/t/f1")
    _reserve(cfg, cap=2)
    p = plan(fold(log.read_all()), cfg, agent="me")
    assert _ready(p) == [] and p.capped == ["F2"]
    cfg.schedule.reserved_slots = 0
    assert _ready(plan(fold(log.read_all()), cfg, agent="me")) == ["F2"]


def test_a_no_worktree_feature_is_not_held_by_the_tree_reservation(log, cfg):
    _queue(log, "F1")
    _task(log, "R", "no-worktree")
    _hold(log, "F1", worktree="/t/f1")
    _reserve(cfg, cap=8)
    cfg.worktree.max_parallel = 2
    assert _ready(plan(fold(log.read_all()), cfg, agent="me")) == ["R"]


# -- what the plan says ----------------------------------------------------------------


def test_a_held_feature_is_told_the_truth(log, cfg):
    """B40386f7a40's lesson: a free slot is never "cap reached", and the sentence names
    what the slot is kept for, so nobody waits for a message that cannot clear."""
    _queue(log, "F1", "F2", "F3")
    _hold(log, "F1", "x")
    _hold(log, "F2", "y")
    _reserve(cfg)
    p = plan(fold(log.read_all()), cfg, agent="me")
    detail = p.blocked[-1].detail
    assert p.blocked[-1].item == "F3" and p.blocked[-1].reason == "state"
    assert "cap reached" not in detail
    assert "schedule.reserved_slots" in detail and "bug fixes" in detail, detail
    assert p.blocked[-1].waiting_on == ["F1", "F2"]


def test_a_full_cap_still_says_cap_reached_under_a_reservation(log, cfg):
    """No slot is free at all: the wording `wait` keys on stays."""
    _queue(log, "F1", "F2", "F3", "F4")
    for i, who in (("F1", "x"), ("F2", "y"), ("F3", "z")):
        _hold(log, i, who)
    _reserve(cfg, cap=3)
    p = plan(fold(log.read_all()), cfg, agent="me")
    assert "cap reached" in p.blocked[-1].detail


def test_a_reservation_hold_does_not_blame_a_cap_that_was_not_reached(log, cfg):
    """Critic round: "held by the worktree cap and schedule.reserved_slots" named a cap with
    a slot still free. Only the reservation held this item."""
    _queue(log, "F1", "F2", "F3")
    _hold(log, "F1", worktree="/t/f1")
    _reserve(cfg, cap=8)
    cfg.worktree.max_parallel = 2
    p = plan(fold(log.read_all()), cfg, agent="me")
    assert p.capped == ["F2", "F3"]
    assert p.cap_note == "schedule.reserved_slots"
    assert "1 held" not in p.summary() and "2 held by schedule.reserved_slots" in p.summary()


def test_the_note_names_the_worktree_share_when_trees_bind_below_the_limit(log, cfg):
    """DX-reserved-note-tree: with worktree.max_parallel tighter than the limit, the
    reservation is a share of the TREE cap, and the summary names that share, not the
    flight share ("1 of 8")."""
    _queue(log, "F1", "F2")
    _hold(log, "F1", worktree="/t/f1")
    _reserve(cfg, cap=8)
    cfg.worktree.max_parallel = 2
    p = plan(fold(log.read_all()), cfg, agent="me")
    assert "1 of 2 worktree slot(s) reserved" in p.summary(), p.summary()
    assert "1 of 8" not in p.summary(), p.summary()


def test_the_note_names_the_flight_share_when_the_tree_cap_does_not_bind(log, cfg):
    """DX-reserved-note-tree (roborev 4508): no-worktree leases keep the FLIGHT limit binding
    even with a tighter worktree cap, so the note must name the flight share, not worktrees."""
    _queue(log, "F1", "F2", "F3", "F4", "F5", "F6")
    for n in ("F1", "F2", "F3", "F4", "F5", "F6"):
        _hold(log, n, worktree="")
    _reserve(cfg, cap=8, reserved=2)
    cfg.worktree.max_parallel = 2
    p = plan(fold(log.read_all()), cfg, agent="me")
    assert "worktree slot(s)" not in p.reserved_note, p.reserved_note
    assert "2 of 8 slot(s) reserved" in p.reserved_note, p.reserved_note


def test_the_cap_note_names_both_when_both_held_something(log, cfg):
    """F3 meets a free slot that is reserved; fix-A then takes it, and F4 meets a full cap."""
    log.append("phase.added", "P", {"title": "phase"})
    for n, id_ in enumerate(("F1", "F2", "F3")):
        _task(log, id_, priority=n + 1)
    _task(log, "fix-A", "bug", priority=4)
    _task(log, "F4", priority=5)
    _reserve(cfg)
    cfg.schedule.bugs_first = False
    p = plan(fold(log.read_all()), cfg, agent="me")
    assert _ready(p) == ["F1", "F2", "fix-A"] and p.capped == ["F3", "F4"]
    assert "the parallelism cap" in p.cap_note and "schedule.reserved_slots" in p.cap_note


def test_no_reservation_leaves_the_cap_note_alone(log, cfg):
    _queue(log, "F1", "F2")
    cfg.schedule.max_parallel_tasks = 1
    assert "reserved" not in plan(fold(log.read_all()), cfg, agent="me").cap_note


def test_the_summary_says_how_many_are_reserved_and_for_what(log, cfg):
    _queue(log, "F1", "F2")
    _reserve(cfg)
    summary = plan(fold(log.read_all()), cfg, agent="me").summary()
    assert "1 of 3 slot(s) reserved for bug fixes and tags ddflow-gap" in summary, summary


def test_the_summary_says_it_once_with_the_parallel_line_set(log, cfg):
    _queue(log, "F1", "F2")
    _reserve(cfg)
    decision = FC.Decision(3, "fixed", "", "", False)
    p = plan(fold(log.read_all()), cfg, agent="me", parallel=decision)
    assert p.summary().count("reserved for") == 1, p.summary()
    assert p.parallel_line.count("reserved for") == 1, p.parallel_line


def test_the_note_drops_the_tags_when_there_are_none(log, cfg):
    _queue(log, "F1")
    _reserve(cfg)
    cfg.schedule.reserved_tags = []
    note = plan(fold(log.read_all()), cfg, agent="me").reserved_note
    assert note.endswith("reserved for bug fixes"), note


def test_reserved_items_are_bug_fixes_and_tagged_tasks(log, cfg):
    _queue(log, "F1")
    _task(log, "fix-B", "bug")
    _task(log, "G", "ddflow-gap")
    assert reserved_items(fold(log.read_all()), cfg) == {"fix-B", "G"}


def _project(repo: Path) -> None:
    assert run_cli(repo, "init")[0] == OK
    assert run_cli(repo, "config", "schedule.parallel", "fixed")[0] == OK
    assert run_cli(repo, "config", "schedule.max_parallel_tasks", "3")[0] == OK
    assert run_cli(repo, "config", "schedule.reserved_slots", "1")[0] == OK
    run_cli(repo, "phase", "add", "P", "--title", "P")
    for n in ("F1", "F2", "F3"):
        run_cli(
            repo, "task", "add", n, "--phase", "P", "--title", f"{n} fixture", "--globs", n.lower()
        )


@pytest.mark.parametrize("args", [("next",), ("status",), ("brief",)])
def test_next_status_and_brief_say_it(repo, args):
    _project(repo)
    code, out, err = run_cli(repo, *args)
    assert code == OK, err
    assert "1 of 3 slot(s) reserved for bug fixes and tags ddflow-gap" in out, out


def test_status_json_carries_it(repo):
    _project(repo)
    code, out, err = run_cli(repo, "--json", "status")
    assert code == OK, err
    assert "1 of 3 slot(s) reserved" in json.loads(out)["parallel"], out


def test_next_with_nothing_ready_still_says_it(repo):
    from ddflow import api

    _project(repo)
    assert api.lifecycle.claim(repo, "F1", no_worktree=True, agent="x").ok
    assert api.lifecycle.claim(repo, "F2", no_worktree=True, agent="y").ok
    code, out, _err = run_cli(repo, "next")
    assert code == 2
    assert "1 of 3 slot(s) reserved" in out and "F3" in out, out


# -- the knobs -------------------------------------------------------------------------


def test_the_defaults_change_nothing():
    cfg = Config()
    assert cfg.schedule.reserved_slots == 0
    assert cfg.schedule.reserved_tags == ["ddflow-gap"]


def test_the_knobs_are_settable_and_a_negative_is_refused(repo):
    assert run_cli(repo, "init")[0] == OK
    assert run_cli(repo, "config", "schedule.reserved_slots", "2")[0] == OK
    assert Config.load(repo).schedule.reserved_slots == 2
    assert run_cli(repo, "config", "schedule.reserved_slots", "-1")[0] != OK
    assert Config.load(repo).schedule.reserved_slots == 2


def test_config_explains_both_knobs(repo):
    assert run_cli(repo, "init")[0] == OK
    code, out, err = run_cli(repo, "--json", "config", "--explain")
    assert code == OK, err
    rows = {r["key"]: r for r in json.loads(out)}
    for key in ("schedule.reserved_slots", "schedule.reserved_tags"):
        assert "reserved" in rows[key]["doc"].lower(), rows[key]


# -- the port --------------------------------------------------------------------------


@pytest.mark.parametrize("adapter", [GreedyV1(), FakeOptimizer()], ids=["greedy", "fake"])
def test_both_adapters_keep_ordinary_work_out_of_the_reserved_share(adapter):
    ready = [
        Candidate("a"),
        Candidate("b"),
        Candidate("c", reserved=True),
        Candidate("d"),
    ]
    r = adapter.cut({}, ready, Slots(3, 3, ordinary_flight=1, ordinary_tree=3))
    assert r.ids("taken") == ["a", "c"]
    assert r.ids("reserved") == ["b", "d"]


@pytest.mark.parametrize("adapter", [GreedyV1(), FakeOptimizer()], ids=["greedy", "fake"])
def test_both_adapters_apply_the_ordinary_tree_share_only_to_tree_work(adapter):
    ready = [Candidate("a"), Candidate("b"), Candidate("n", needs_tree=False)]
    r = adapter.cut({}, ready, Slots(3, 3, ordinary_flight=3, ordinary_tree=1))
    assert r.ids("taken") == ["a", "n"] and r.ids("reserved") == ["b"]


@pytest.mark.parametrize("adapter", [GreedyV1(), FakeOptimizer()], ids=["greedy", "fake"])
def test_no_ordinary_share_means_the_old_cut(adapter):
    r = adapter.cut({}, [Candidate("a"), Candidate("b")], Slots(1, 1))
    assert r.ids("taken") == ["a"] and r.ids("cut") == ["b"] and not r.ids("reserved")


@pytest.mark.parametrize("adapter", [GreedyV1(), FakeOptimizer()], ids=["greedy", "fake"])
def test_a_full_cap_is_a_cut_for_reserved_and_ordinary_alike(adapter):
    ready = [Candidate("a", reserved=True), Candidate("b", reserved=True), Candidate("c")]
    r = adapter.cut({}, ready, Slots(1, 1, ordinary_flight=0, ordinary_tree=0))
    assert r.ids("taken") == ["a"] and r.ids("cut") == ["b", "c"]


# -- the adaptive limit ----------------------------------------------------------------


GOOD = {"load": 0.05, "memory_free_frac": 0.9, "disk_free_frac": 0.9}


def _grow(repo, log, cfg, flying: list[str], reserved: int) -> tuple[int, dict]:
    """The limit after fifteen healthy minutes with ``flying`` in flight, and the first row
    (growth changes what later rows see)."""
    log.append("phase.added", "P", {"title": "phase"})
    for i in [*flying, "F8", "F9"]:
        _task(log, i, *(["bug"] if i.startswith("fix") else []))
    now = [time.time()]
    for i in flying:
        log.append("lease.acquired", i, {"holder": i, "at": now[0], "ttl_s": 10**6})
        log.append("item.started", i, {})
    cfg.schedule.max_parallel_tasks = 4
    cfg.schedule.reserved_slots = reserved
    ctx = F.FlowCtx(repo=repo, cfg=cfg, state=fold(log.read_all()))
    src = SIG.FakeSource(GOOD)
    for _ in range(15):
        assert F.sample_if_due(ctx, src, lambda: now[0]).written
        now[0] += 60
    row = json.loads(F.ring_path(repo).read_text("utf-8").splitlines()[0])
    return F.current_limit(ctx, None, lambda: now[0]).limit, row


def test_an_idle_reserved_slot_does_not_stop_the_limit_growing(repo, log, cfg):
    """Auto grows only while in flight reaches the limit. An idle reserved slot keeps it one
    short, so the features held out would never see the limit rise to admit them."""
    limit, row = _grow(repo, log, cfg, ["F1", "F2", "F3"], reserved=1)
    assert limit > 4
    assert row["reserved_idle"] == 1 and row["limit_binding"] is True


def test_the_limit_does_not_grow_on_ordinary_slots_still_open(repo, log, cfg):
    """A reservation is no demand: with an ordinary slot open nothing is held back."""
    limit, row = _grow(repo, log, cfg, ["F1", "F2"], reserved=1)
    assert limit == 4
    assert row["reserved_idle"] == 0 and row["limit_binding"] is False


def test_a_reserved_slot_in_use_is_not_idle(repo, log, cfg):
    _limit, row = _grow(repo, log, cfg, ["F1", "F2", "fix-B"], reserved=2)
    assert row["reserved_idle"] == 1 and row["in_flight"] == 3


def test_a_slot_a_claim_by_name_took_is_not_idle(log, cfg):
    """`claim` applies no cap: three features on limit 3 leave no reserved slot to be idle."""
    _queue(log, "F1", "F2", "F3")
    cfg.schedule.reserved_slots = 1
    st = fold(log.read_all())
    assert idle_reserved(st, cfg, ["F1", "F2", "F3"], 3) == 0
    assert idle_reserved(st, cfg, ["F1", "F2"], 3) == 1


def test_nothing_reserved_counts_nothing_idle(repo, log, cfg):
    limit, row = _grow(repo, log, cfg, ["F1", "F2", "F3"], reserved=0)
    assert limit == 4 and row["reserved_idle"] == 0

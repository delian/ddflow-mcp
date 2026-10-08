"""plan() as a pipeline of named filters and ordering keys, and one admission check.

Pinned first (D-unify 4): a golden of what `plan` decides over a scenario that exercises
every filter (dependencies, a cycle, a missing dependency, a held lease and a glob clash
with it, the parallelism cap, two offered items that overlap, bugs first), then the
interface: `admission.glob_conflict` (the four copies of the glob loop), the registries
(`FILTERS`, `KEYS`, `ORDER`) and `plan_for` (one answer for every caller, differing only by
named purpose). Property tests (hypothesis): the offer never contains two overlapping
items, never offers an item before its dependencies, and is deterministic.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ddflow.api.lifecycle import PURPOSES, plan_for
from ddflow.config import Config
from ddflow.core import admission
from ddflow.core import schedule as S
from ddflow.core.model import fold
from ddflow.core.schedule import conflicts, plan

GOLDEN = Path(__file__).parent / "golden" / "plan_pipeline.json"


def _scenario(log) -> None:
    A = log.append
    A("phase.added", "P", {"title": "phase"})
    A("phase.added", "Q", {"title": "other"})
    A("task.added", "T1", {"parent": "P", "globs": ["a/**"], "priority": 50})
    A("task.added", "T2", {"parent": "P", "globs": ["a/x.py"], "priority": 10})
    A("task.added", "T3", {"parent": "P", "globs": ["b/**"], "needs": ["T1"]})
    A("task.added", "T4", {"parent": "P", "globs": ["c/**"], "priority": 30})
    A("task.added", "T5", {"parent": "P", "globs": ["d/**"], "priority": 30})
    A("task.added", "T6", {"parent": "P", "globs": ["e/**"], "needs": ["T7"]})
    A("task.added", "T7", {"parent": "P", "globs": ["f/**"], "needs": ["T6"]})
    A("task.added", "T8", {"parent": "P", "globs": ["held/**"]})
    A("task.added", "T9", {"parent": "P", "globs": ["held/sub.py"]})
    A("task.added", "T10", {"parent": "Q", "globs": ["g/**"], "tags": ["no-worktree"]})
    A("task.added", "T11", {"parent": "Q", "globs": ["h/**"], "needs": ["Missing"]})
    A("task.added", "T12", {"parent": "Q", "globs": ["i/**"], "tags": ["bugfix"], "priority": 99})
    A("task.added", "T13", {"parent": "Q", "globs": ["j/**"], "needs": ["Q:T1"]})
    A(
        "lease.acquired",
        "T8",
        {"holder": "other", "at": time.time(), "ttl_s": 600, "globs": ["held/**"]},
    )
    A("item.started", "T8", {})
    A("bug.found", "Bx", {"item": "T5", "summary": "broken"})


def _snap(p) -> dict:
    return json.loads(
        json.dumps(
            {
                "ready": [i.id for i in p.ready],
                "blocked": [(b.item, b.reason, b.detail, b.waiting_on) for b in p.blocked],
                "running": [i.id for i in p.running],
                "cycles": p.cycles,
                "interrupted": p.interrupted,
                "review": [i.id for i in p.review],
                "capped": p.capped,
                "cap_note": p.cap_note,
                "overlapped": p.overlapped,
                "parallel_line": p.parallel_line,
                "finished": p.finished,
                "summary": p.summary(),
            },
            default=str,
        )
    )


def test_the_plan_is_unchanged_by_the_pipeline(log):
    """The golden was recorded from the monolithic plan() before it was cut into filters."""
    _scenario(log)
    state = fold(log.read_all())
    got = {}
    for name, kw in {
        "default": {},
        "phaseP": {"phase": "P"},
        "me": {"agent": "other"},
        "bugsoff": {},
    }.items():
        c = Config.load()
        if name == "bugsoff":
            c.schedule.bugs_first = False
        c.schedule.max_parallel_tasks = 4
        got[name] = _snap(plan(state, c, now=time.time(), **kw))
    c = Config.load()
    c.schedule.max_parallel_tasks = 2
    c.worktree.max_parallel = 8
    got["cap2"] = _snap(plan(state, c, agent="me"))
    c.schedule.ready_policy = "deps_only"
    got["depsonly"] = _snap(plan(state, c, agent="me"))
    assert got == json.loads(GOLDEN.read_text())


def test_the_registries_name_the_pipeline():
    assert list(S.FILTERS) == ["deps", "lease", "globs", "resources"]
    assert S.LEASE_FILTERS == {"lease", "globs", "resources"}
    assert S.ORDER == ["bugs_first", "priority", "id"]
    assert set(S.ORDER) <= set(S.KEYS)


def test_a_registered_filter_and_key_take_part_in_the_plan(log, cfg, monkeypatch):
    """A new rule is a registration, not an edit of plan(): this one passes over T2 with
    its own reason, and this key offers the highest id first."""
    _scenario(log)
    monkeypatch.setattr(S, "FILTERS", dict(S.FILTERS))
    monkeypatch.setattr(S, "KEYS", dict(S.KEYS))
    monkeypatch.setattr(S, "ORDER", list(S.ORDER))
    S.register_filter(
        "no_t2",
        lambda ctx, it: S.Blocked(it.id, "state", "test says no") if it.id == "T2" else None,
    )
    S.register_key("late_first", lambda ctx, it: -len(it.id), before="priority")
    p = plan(fold(log.read_all()), cfg, agent="me")
    assert any(b.item == "T2" and b.detail == "test says no" for b in p.blocked)
    assert "T2" not in [i.id for i in p.ready]
    assert S.ORDER == ["bugs_first", "late_first", "priority", "id"]


def test_glob_conflict_live_skips_own_item_holder_and_other_lines(log, cfg):
    _scenario(log)
    state = fold(log.read_all())
    live = state.active_leases(time.time(), cfg.lease.grace_s)
    t9 = state.items["T9"]
    hit = admission.glob_conflict(state, cfg, t9, list(t9.globs), "me", against="live", live=live)
    assert hit is not None and hit.item == "T8" and hit.lease.holder == "other"
    assert hit.pair == ("held/sub.py", "held/**")
    # the holder is not in conflict with itself, and an item is not with its own lease
    assert (
        admission.glob_conflict(state, cfg, t9, list(t9.globs), "other", against="live", live=live)
        is None
    )
    t8 = state.items["T8"]
    assert (
        admission.glob_conflict(state, cfg, t8, list(t8.globs), "me", against="live", live=live)
        is None
    )


def test_glob_conflict_offered_checks_the_items_given(log, cfg):
    _scenario(log)
    state = fold(log.read_all())
    t1, t2, t4 = (state.items[i] for i in ("T1", "T2", "T4"))
    hit = admission.glob_conflict(
        state, cfg, t2, list(t2.globs), against="offered", others=[t4, t1]
    )
    assert hit is not None and hit.item == "T1" and hit.lease is None
    assert (
        admission.glob_conflict(state, cfg, t2, list(t2.globs), against="offered", others=[t4])
        is None
    )


def test_shared_globs_overlap_nothing(log, cfg):
    cfg.lease.shared_globs = ["CHANGELOG.md"]
    log.append("task.added", "A", {"globs": ["CHANGELOG.md", "x/**"]})
    log.append("task.added", "B", {"globs": ["CHANGELOG.md", "y/**"]})
    state = fold(log.read_all())
    a, b = state.items["A"], state.items["B"]
    assert (
        admission.glob_conflict(state, cfg, a, list(a.globs), against="offered", others=[b]) is None
    )
    assert conflicts(["CHANGELOG.md"], ["CHANGELOG.md"], ["CHANGELOG.md"]) == []


def test_the_old_import_paths_still_work():
    from ddflow.core.schedule import conflicts as c1
    from ddflow.core.schedule import is_shared as s1
    from ddflow.core.schedule import shared_globs as g1

    assert (c1, s1, g1) == (admission.conflicts, admission.is_shared, admission.shared_globs)


def test_plan_for_gives_every_caller_the_same_offer(repo, log, cfg):
    """status/brief/workflow_state ('view') report the offer `next` ('offer') makes; the
    queue's bare shape ('structure') differs only in having no reservation or limit."""
    _scenario(log)
    state = fold(log.read_all())
    offer = plan_for(repo, log, cfg, state, purpose="offer", agent="me")
    view = plan_for(repo, log, cfg, state, purpose="view", agent="me")
    assert [i.id for i in offer.ready] == [i.id for i in view.ready]
    assert _snap(offer) == _snap(view)
    assert set(PURPOSES) == {"offer", "view", "structure"}
    with pytest.raises(ValueError, match="unknown plan purpose"):
        plan_for(repo, log, cfg, state, purpose="nonsense")


def test_a_view_does_not_call_ready_what_a_waiter_holds_back(repo, log, cfg):
    """The disagreement this task fixes: `next` withheld an item reserved for a waiter in
    line while `status` and `brief` still called it ready."""
    from ddflow.services import waits as WT

    cfg.lease.waiter_reservation_s = 600
    log.append("task.added", "W", {"globs": ["w/**"]})
    log.append("task.added", "Z", {"globs": ["z/**"]})
    state = fold(log.read_all())
    WT.register(
        repo,
        WT.Waiter(
            agent="waiter",
            item="W",
            waiting_on=["HOT"],
            since=time.time() - 60,
            until=time.time() + 600,
        ),
    )
    bare = plan_for(repo, log, cfg, state, purpose="structure", agent="me")
    view = plan_for(repo, log, cfg, state, purpose="view", agent="me")
    offer = plan_for(repo, log, cfg, state, purpose="offer", agent="me")
    assert "W" not in {i.id for i in offer.ready}
    assert {i.id for i in view.ready} == {i.id for i in offer.ready}
    assert {"W", "Z"} <= {i.id for i in bare.ready}


# -- properties ---------------------------------------------------------------------------

_GLOBS = ["a/**", "a/x.py", "b/**", "b/y/**", "c.py", "d/**"]


@st.composite
def _queues(draw):
    n = draw(st.integers(1, 9))
    items = []
    for i in range(n):
        items.append(
            {
                "id": f"T{i}",
                "globs": draw(
                    st.lists(st.sampled_from(_GLOBS), min_size=1, max_size=2, unique=True)
                ),
                "needs": draw(st.lists(st.sampled_from([f"T{j}" for j in range(i)]), unique=True))
                if i
                else [],
                "priority": draw(st.integers(0, 3)),
                "held": draw(st.booleans()),
                "done": draw(st.booleans()),
            }
        )
    return items, draw(st.integers(1, 6))


def _build(log, items) -> None:
    log.append("phase.added", "P", {"title": "p"})
    for it in items:
        log.append(
            "task.added",
            it["id"],
            {"parent": "P", "globs": it["globs"], "needs": it["needs"], "priority": it["priority"]},
        )
    for it in items:
        if it["done"]:
            log.append("item.completed", it["id"], {})
        elif it["held"]:
            log.append(
                "lease.acquired",
                it["id"],
                {"holder": "other", "at": time.time(), "ttl_s": 600, "globs": it["globs"]},
            )
            log.append("item.started", it["id"], {})


@settings(
    max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(_queues())
def test_the_offer_never_conflicts_never_precedes_its_deps_and_is_deterministic(repo, queue):
    from ddflow.infra.log import EventLog

    items, cap = queue
    import tempfile

    from ddflow.config import Config as C

    with tempfile.TemporaryDirectory() as d:
        import subprocess

        subprocess.run(["git", "init", "-q", d], check=True)
        lg = EventLog(Path(d), "agent-test")
        _build(lg, items)
        cfg = C.load()
        cfg.schedule.max_parallel_tasks = cap
        state = fold(lg.read_all())
        now = time.time()
        first = plan(state, cfg, agent="me", now=now)
        again = plan(state, cfg, agent="me", now=now)
        assert _snap(first) == _snap(again)
        ready = first.ready
        assert len(ready) <= cap
        for i, a in enumerate(ready):
            for b in ready[i + 1 :]:
                assert not conflicts(list(a.globs), list(b.globs)), (a.id, b.id)
            for dep in a.needs:
                assert state.items[dep].state == "done", (a.id, dep)
            for lease in state.active_leases(now, cfg.lease.grace_s).values():
                assert not conflicts(list(a.globs), lease.globs) or lease.holder == "me", a.id


def test_brief_does_not_suggest_what_next_holds_back_for_a_waiter(repo, log):
    """B-uni-plan-pipeline: `brief` planned without the reservation hold, so it headed the
    brief with an item reserved for a waiter that `next` would not offer."""
    from ddflow.api import lifecycle as LC
    from ddflow.services import waits as WT

    log.append("task.added", "W", {"globs": ["w/**"], "priority": 1})
    log.append("task.added", "Z", {"globs": ["z/**"], "priority": 9})
    cfg_path = repo / ".ddflow"
    cfg_path.mkdir(exist_ok=True)
    (cfg_path / "config.toml").write_text("[lease]\nwaiter_reservation_s = 600\n")
    WT.register(
        repo,
        WT.Waiter(
            agent="waiter",
            item="W",
            waiting_on=["HOT"],
            since=time.time() - 60,
            until=time.time() + 600,
        ),
    )
    nxt = LC.next_(repo, agent="me")
    assert [r["id"] for r in nxt.data["ready"]] == ["Z"]
    b = LC.brief(repo, agent="me")
    assert b.data.get("item") == "Z", b.data

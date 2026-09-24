import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orchard.model import fold
from orchard.schedule import critical_path, find_cycles, globs_overlap, plan


def build(log, spec):
    log.append("phase.added", "P1", {"title": "phase"})
    for tid, needs, globs in spec:
        log.append("task.added", tid, {"parent": "P1", "needs": needs, "globs": globs})
    return fold(log.read_all())


def test_fold_is_deterministic(log):
    build(log, [("T1", [], ["a/*"]), ("T2", ["T1"], ["b/*"])])
    evs = log.read_all()
    a, b = fold(evs), fold(sorted(evs, key=lambda e: e.sort_key(), reverse=True))
    assert set(a.items) == set(b.items)
    assert fold(evs).items["T2"].needs == ["T1"]


def test_unknown_dependency_blocks_by_default(log, cfg):
    st = build(log, [("T1", ["NOPE"], ["a/*"])])
    p = plan(st, cfg, phase="P1")
    assert not p.ready
    assert "unknown dependency" in p.blocked[0].detail


def test_unknown_dependency_can_be_downgraded(log, cfg):
    st = build(log, [("T1", ["NOPE"], ["a/*"])])
    cfg.schedule.unknown_dep_policy = "warn"
    assert [i.id for i in plan(st, cfg, phase="P1").ready] == ["T1"]


def test_cycles_are_detected_and_reported_once(log, cfg):
    st = build(log, [("T1", ["T3"], ["a/*"]), ("T2", ["T1"], ["b/*"]), ("T3", ["T2"], ["c/*"])])
    cycles = find_cycles(dict(st.items.items()))
    assert len(cycles) == 1
    assert set(cycles[0]) == {"T1", "T2", "T3"}
    p = plan(st, cfg, phase="P1")
    assert not p.ready and all(b.reason == "cycle" for b in p.blocked)


def test_parallel_fanout_respects_the_cap(log, cfg):
    st = build(log, [(f"T{i}", [], [f"d{i}/*"]) for i in range(6)])
    cfg.schedule.max_parallel_tasks = 3
    cfg.worktree.max_parallel = 3
    p = plan(st, cfg, phase="P1")
    assert len(p.ready) == 3
    assert any(b.reason == "state" and "cap" in b.detail for b in p.blocked)


def test_glob_conflict_blocks_but_names_the_holder(log, cfg):
    st = build(
        log,
        [("T1", [], ["src/auth/*"]), ("T2", [], ["src/auth/login.py"]), ("T3", [], ["src/ui/*"])],
    )
    log.append(
        "lease.acquired",
        "T1",
        {"holder": "other", "at": time.time(), "ttl_s": 600, "globs": ["src/auth/*"]},
    )
    st = fold(log.read_all())
    p = plan(st, cfg, phase="P1", agent="me")
    assert [i.id for i in p.ready] == ["T3"]
    clash = next(b for b in p.blocked if b.item == "T2")
    assert clash.reason == "conflict" and "other" in clash.detail


def test_expired_lease_stops_blocking(log, cfg):
    st = build(log, [("T1", [], ["a/*"])])
    log.append(
        "lease.acquired",
        "T1",
        {"holder": "ghost", "at": time.time() - 99999, "ttl_s": 60, "globs": ["a/*"]},
    )
    st = fold(log.read_all())
    p = plan(st, cfg, phase="P1", agent="me")
    assert [i.id for i in p.ready] == ["T1"], "an expired lease must not block forever"


def test_critical_path_is_the_longest_chain(log):
    st = build(log, [("T1", [], []), ("T2", ["T1"], []), ("T3", ["T2"], []), ("T4", [], [])])
    assert critical_path(st, "P1") == ["T1", "T2", "T3"]


@pytest.mark.parametrize(
    "a,b,want",
    [
        ("src/*", "src/a.py", True),
        ("src/a/*", "src/a/b/c.py", True),
        ("src/ui/*", "src/api/*", False),
        ("*", "anything", True),
        ("a.py", "a.py", True),
        ("docs/*.md", "src/*.py", False),
    ],
)
def test_glob_overlap_errs_toward_yes(a, b, want):
    assert globs_overlap(a, b) is want


def test_a_stale_renewal_cannot_hijack_the_current_holders_worktree(log):
    """A former holder's `lease.renewed`, ordered late by a shard merge, must be ignored.

    Lamport values are computed independently on unsynced clones, so merging two shards
    can place A's old renewal AFTER B's later acquisition. Applying it would point B's
    live lease at A's dead worktree — which `recover`, `merge` and `worktree remove` all
    then target. Found by adversarial review; it was introduced by the fix that made
    renewal carry the worktree at all. Mutation-verified: dropping the `stale` guard in
    `fold` makes this red.
    """
    from orchard.events import Event
    from orchard.model import fold

    def ev(lamport, agent, kind, data):
        e = Event(
            kind=kind,
            subject="T1",
            data=data,
            agent=agent,
            lamport=lamport,
            ts="2026-01-01T00:00:00Z",
        )
        return Event(**{**e.__dict__, "id": e.compute_id()})

    events = [
        ev(1, "ctl", "task.added", {"parent": "P1"}),
        ev(
            2,
            "A",
            "lease.acquired",
            {"holder": "A", "at": 1.0, "ttl_s": 999, "worktree": "/wtA", "branch": "b-A"},
        ),
        ev(3, "A", "lease.released", {"holder": "A"}),
        ev(
            4,
            "B",
            "lease.acquired",
            {"holder": "B", "at": 2.0, "ttl_s": 999, "worktree": "/wtB", "branch": "b-B"},
        ),
        # A's stale renewal, sorted last after a merge:
        ev(
            5,
            "A",
            "lease.renewed",
            {"holder": "A", "at": 3.0, "worktree": "/stale-wtA", "branch": "stale-A"},
        ),
    ]
    st = fold(events)
    lease = st.items["T1"].lease
    assert lease.holder == "B"
    assert lease.worktree == "/wtB", "a former holder overwrote the live lease"
    assert lease.branch == "b-B"
    assert st.items["T1"].worktree == "/wtB"

"""Concurrency at scale — the property every other guarantee rests on.

These are slow by design (real processes, real locks). They exist because the coordination
bugs this system prevents are all *rare-interleaving* bugs: they do not reproduce at
n=2, and a design that is merely argued to be correct has, historically, not been.
"""

from __future__ import annotations

import multiprocessing as mp
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orchard import lease as L
from orchard.config import Config
from orchard.events import EventLog
from orchard.model import fold
from orchard.schedule import conflicts, plan

N_AGENTS = 8
N_TASKS = 32


def _seed(repo: Path) -> None:
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "stress"})
    for i in range(N_TASKS):
        # Every 4th task shares a glob family with its neighbour, so the conflict
        # detector is genuinely exercised rather than trivially satisfied.
        family = i // 4
        log.append(
            "task.added", f"T{i:02d}", {"parent": "P1", "globs": [f"src/mod{family}/f{i}.py"]}
        )


def _agent(args) -> list[tuple[str, float, float]]:
    """One agent: repeatedly take whatever is offered, hold it briefly, finish it."""
    root, name, deadline = args
    cfg = Config.load()
    cfg.lease.ttl_s = 60
    cfg.schedule.max_parallel_tasks = N_TASKS
    cfg.worktree.max_parallel = N_TASKS
    log = EventLog(Path(root), name)
    held: list[tuple[str, float, float]] = []
    while time.time() < deadline:
        st = fold(log.read_all(), strict=False)
        p = plan(st, cfg, phase="P1", agent=name)
        if not p.ready:
            if not [i for i in st.tasks("P1") if i.state != "done"]:
                break
            time.sleep(0.01)
            continue
        target = p.ready[0].id
        try:
            L.acquire(log, cfg, target, holder=name)
        except L.LeaseError:
            continue
        t0 = time.time()
        log.append("item.started", target, {})
        time.sleep(0.005)
        log.append("item.completed", target, {"sha": f"sha-{target}"})
        L.release(log, target, holder=name)
        held.append((target, t0, time.time()))
    return held


@pytest.mark.slow
def test_many_agents_drain_a_queue_without_conflict_or_loss(repo):
    """8 processes, 32 tasks with overlapping globs, one shared log."""
    _seed(repo)
    deadline = time.time() + 60
    with mp.Pool(N_AGENTS) as pool:
        results = pool.map(_agent, [(str(repo), f"agent{i}", deadline) for i in range(N_AGENTS)])

    log = EventLog(repo, "verify")
    st = fold(log.read_all(), strict=False)

    done = [t for t in st.tasks("P1") if t.state == "done"]
    assert len(done) == N_TASKS, (
        f"only {len(done)}/{N_TASKS} tasks completed — the queue did not drain"
    )

    claims = [c for agent in results for c in agent]
    assert len(claims) == N_TASKS, (
        f"{len(claims)} claims for {N_TASKS} tasks — a task was done twice"
    )
    assert len({c[0] for c in claims}) == N_TASKS, "an item was claimed by two agents"

    assert not log.verify(), f"log integrity problems: {log.verify()}"
    assert len({e.id for e in log.read_all()}) == len(log.read_all()), "duplicate event id"

    # More than one agent must actually have worked, or the test proved nothing about
    # concurrency — it would have measured a queue drained serially by one winner.
    workers = {i for i, agent in enumerate(results) if agent}
    assert len(workers) >= 2, "no real concurrency occurred; the test is vacuous"


@pytest.mark.slow
def test_no_two_overlapping_globs_are_ever_held_at_once(repo):
    """The safety property itself, checked against the recorded intervals.

    Reconstructed from the log rather than sampled during the run: sampling can miss a
    violation that opens and closes between two polls, whereas the intervals are a
    complete record of who held what, when.
    """
    _seed(repo)
    deadline = time.time() + 60
    with mp.Pool(N_AGENTS) as pool:
        results = pool.map(_agent, [(str(repo), f"agent{i}", deadline) for i in range(N_AGENTS)])
    st = fold(EventLog(repo, "v").read_all(), strict=False)
    globs = {t.id: t.globs for t in st.tasks("P1")}

    intervals = sorted((c for agent in results for c in agent), key=lambda c: c[1])
    violations = []
    for i, (a_id, _a_start, a_end) in enumerate(intervals):
        for b_id, b_start, _b_end in intervals[i + 1 :]:
            if b_start >= a_end:
                break  # sorted by start: no later one can overlap
            if conflicts(globs.get(a_id, []), globs.get(b_id, [])):
                violations.append((a_id, b_id))
    assert not violations, f"overlapping globs held simultaneously: {violations[:5]}"

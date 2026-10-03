"""B169: the warm read maintains its Lamport order incrementally.

`read_all` used to re-sort every event and re-hash every id on each call, so a warm read
was linear in the LOG (11.9 ms at 20k events, 62.8 ms at 100k) rather than in what was
appended. These tests pin two things: the incremental order is IDENTICAL to the
from-scratch one under every awkward shape of log (duplicates, out-of-order shards, torn
tails, rewrites, deletions), and the warm read is measurably cheaper than re-sorting.
"""

from __future__ import annotations

import dataclasses
import random
import statistics
import time

import pytest

from ddflow.config import LogConfig
from ddflow.core.events import Event
from ddflow.infra import log as L
from ddflow.infra.log import EventLog, clear_parse_cache


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(EventLog, "stamp", False)
    clear_parse_cache()
    yield
    clear_parse_cache()


def _ev(agent: str, lamport: int, tag: str = "") -> Event:
    e = Event(
        kind="task.added",
        subject=f"T{lamport}{tag}",
        data={"title": "t", "kind": "task"},
        agent=agent,
        lamport=lamport,
        ts="2026-10-03T00:00:00Z",
    )
    return dataclasses.replace(e, id=e.compute_id())


def _put(log: EventLog, agent: str, events: list[Event], mode: str = "ab") -> None:
    log.dir.mkdir(parents=True, exist_ok=True)
    with (log.dir / f"{agent}.jsonl").open(mode) as fh:
        for e in events:
            fh.write((e.to_json() + "\n").encode())


def _same(repo, cached: EventLog) -> None:
    plain = EventLog(repo, "x", log_cfg=LogConfig(reuse_parsed=False))
    want = plain.read_all()
    got = cached.read_all()
    assert [e.id for e in got] == [e.id for e in want]
    assert cached.skipped_lines == plain.skipped_lines


def test_random_workload_matches_the_unoptimised_path_after_every_step(repo):
    rng = random.Random(7)
    log = EventLog(repo, "x")
    clocks = {"a": 0, "b": 0, "c": 0}
    for step in range(120):
        agent = rng.choice(list(clocks))
        n = rng.choice([1, 1, 2, 5, 70])  # 70 exceeds the incremental batch limit
        if rng.random() < 0.3:
            clocks[agent] = rng.randint(0, clocks[agent] + 3)  # out-of-order shard
        evs = []
        for _ in range(n):
            clocks[agent] += rng.randint(0, 3)  # ties and repeats of a Lamport value
            evs.append(_ev(agent, clocks[agent], str(rng.randint(0, 5))))
        _put(log, agent, evs)
        _same(repo, log)


def test_a_duplicate_event_across_shards_is_kept_once(repo):
    log = EventLog(repo, "x")
    base = [_ev("a", i) for i in range(1, 30)]
    _put(log, "a", base)
    _same(repo, log)
    _put(log, "b", base[10:20])  # a merged copy of events a already holds
    _same(repo, log)
    assert len(log.read_all()) == len(base)
    _put(log, "a", [base[3]])  # and a repeat inside one shard
    _same(repo, log)
    assert len(log.read_all()) == len(base)


def test_a_torn_tail_never_enters_the_maintained_order(repo):
    log = EventLog(repo, "x")
    _put(log, "a", [_ev("a", i) for i in range(1, 6)])
    _same(repo, log)
    full = _ev("a", 6).to_json().encode()
    with (log.dir / "a.jsonl").open("ab") as fh:
        fh.write(full[:-10])
    _same(repo, log)
    assert len(log.read_all()) == 5
    with (log.dir / "a.jsonl").open("ab") as fh:
        fh.write(full[-10:] + b"\n")
    _same(repo, log)
    assert len(log.read_all()) == 6


def test_a_rewritten_or_deleted_shard_is_not_served_stale(repo):
    log = EventLog(repo, "x")
    _put(log, "a", [_ev("a", i) for i in range(1, 20)])
    _put(log, "b", [_ev("b", i) for i in range(1, 20)])
    _same(repo, log)
    # a branch switch rewrites the file in place with other content of >= length
    _put(log, "a", [_ev("a", i, "other") for i in range(1, 25)], mode="wb")
    _same(repo, log)
    (log.dir / "b.jsonl").unlink()
    _same(repo, log)
    _put(log, "b", [_ev("b", 5, "reborn")])
    _same(repo, log)


def test_the_returned_list_is_a_copy_the_caller_may_mutate(repo):
    log = EventLog(repo, "x")
    _put(log, "a", [_ev("a", i) for i in range(1, 6)])
    first = log.read_all()
    first.clear()
    assert len(log.read_all()) == 5


def test_a_cache_disabled_log_never_builds_a_merged_order(repo):
    log = EventLog(repo, "x", log_cfg=LogConfig(reuse_parsed=False))
    _put(log, "a", [_ev("a", i) for i in range(1, 6)])
    log.read_all()
    assert not L._MERGED


def _bench(repo, n: int) -> tuple[float, float]:
    """(old-style sort+dedupe over n events, warm read after a 1-event append), ms."""
    log = EventLog(repo, "x")
    _put(log, "a", [_ev("a", i) for i in range(1, n // 2 + 1)])
    _put(log, "b", [_ev("b", i) for i in range(1, n // 2 + 1)])
    log.read_all()  # warm
    # What the old read sorted: the raw per-shard concatenation, not the sorted output.
    flat = [e for p in log.shards() for e in log._read_shard(p)[0]]
    old, new = [], []
    for k in range(5):
        t = time.perf_counter()
        L._sorted_unique(list(flat))
        old.append((time.perf_counter() - t) * 1000)
    for k in range(5):
        _put(log, "a", [_ev("a", n + k + 1)])
        t = time.perf_counter()
        log.read_all()
        new.append((time.perf_counter() - t) * 1000)
    return statistics.median(old), statistics.median(new)


def test_the_warm_read_is_cheaper_than_re_sorting_and_does_not_scale_with_sort(repo):
    old, new = _bench(repo, 20_000)
    print(f"20k events: sort+dedupe {old:.1f} ms, warm read {new:.1f} ms")
    assert new < old, (old, new)


def test_distinct_events_never_tie_on_sort_key(repo):
    """Reviewer claim refuted: the key ends in the content id, so two DISTINCT events can
    never tie, and an equal key means a duplicate (which forces a rebuild). Incremental
    placement therefore cannot disagree with a stable sort on ties."""
    log = EventLog(repo, "x")
    _put(log, "a", [_ev("a", 7, "1")])
    _put(log, "b", [_ev("b", 7, "1")])
    _same(repo, log)
    _put(log, "a", [_ev("a", 7, "2")])  # same Lamport value, earlier shard
    _same(repo, log)
    _put(log, "a", [_ev("b", 7, "1")])  # an event identical to b's, appended to a
    _same(repo, log)
    assert len(log.read_all()) == 3


def test_a_warm_read_after_one_append_does_not_re_key_the_log(repo, monkeypatch):
    """Deterministic form of the B169 claim: work per warm read tracks what was appended.

    The old read sorted every event, i.e. called `sort_key` once per event per read.
    """
    log = EventLog(repo, "x")
    n = 3000
    _put(log, "a", [_ev("a", i) for i in range(1, n + 1)])
    log.read_all()
    calls = 0
    real = Event.sort_key

    def counting(self):
        nonlocal calls
        calls += 1
        return real(self)

    monkeypatch.setattr(Event, "sort_key", counting)
    _put(log, "a", [_ev("a", n + 1)])
    assert len(log.read_all()) == n + 1
    assert calls < 50, f"{calls} sort_key calls for one appended event over {n}"
    calls = 0
    _put(log, "b", [_ev("b", 1500)])  # lands mid-order: bisection, still not a re-sort
    assert len(log.read_all()) == n + 2
    assert calls < 50, calls

"""The log is the source of truth, so its guarantees are the ones that matter most."""

import json
import multiprocessing as mp
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _wait
from _wait import scaled

from ddflow.infra.log import Event, EventLog, canonical


@pytest.fixture(autouse=True)
def _no_version_stamp(monkeypatch):
    """These tests are about the log's storage mechanics (event counts, byte offsets, clock
    values); the version stamp (B-upgrade.1-stamp) is tested in tests/test_upgrade_stamp.py."""
    monkeypatch.setattr(EventLog, "stamp", False)


def test_event_id_is_a_content_address(log):
    e = log.append("phase.added", "P1", {"title": "x"})
    assert e.id == e.compute_id()
    tampered = Event(**{**e.__dict__, "data": {"title": "y"}})
    assert tampered.compute_id() != e.id, "editing content must change the address"


def test_verify_detects_a_hand_edited_event(log, repo):
    log.append("phase.added", "P1", {"title": "original"})
    shard = log.shard
    line = json.loads(shard.read_text().strip())
    line["data"]["title"] = "tampered"
    shard.write_text(json.dumps(line) + "\n")
    problems = log.verify()
    assert problems and "does not match its address" in problems[0]


def test_unknown_kind_is_refused(log):
    with pytest.raises(ValueError, match="unknown event kind"):
        log.append("phase.exploded", "P1", {})


def test_lamport_orders_across_agents(repo):
    a, b = EventLog(repo, "a"), EventLog(repo, "b")
    a.append("phase.added", "P1", {})
    b.append("task.added", "T1", {})  # b saw a's event, so must sort after
    a.append("task.added", "T2", {})
    order = [(e.lamport, e.agent) for e in a.read_all()]
    assert order == sorted(order), "lamport clock must produce a total order"
    assert [e.subject for e in a.read_all()] == ["P1", "T1", "T2"]


def test_read_is_idempotent_and_deduplicates(repo):
    a = EventLog(repo, "a")
    a.append("phase.added", "P1", {})
    # Simulate a git merge bringing the same shard in under another name.
    (a.dir / "a-copy.jsonl").write_text(a.shard.read_text())
    assert len(a.read_all()) == 1, "a union of logs must be idempotent"


def test_torn_line_is_skipped_not_fatal(log):
    log.append("phase.added", "P1", {})
    with log.shard.open("a") as fh:
        fh.write('{"kind":"task.added","subject":"T1"')  # crash mid-append
    events = log.read_all()
    assert len(events) == 1
    assert log.skipped_lines == 1
    assert any("torn append" in p for p in log.verify())


_SHARED: dict = {}  # filled in each pool worker: the count of writers that have begun


def _init_worker(counter):
    _SHARED["started"] = counter


def _writer(args):
    root, name, n, lock_s = args
    lg = EventLog(Path(root), name, lock_timeout_s=lock_s)
    if (started := _SHARED.get("started")) is not None:
        with started.get_lock():
            started.value += 1
    for i in range(n):
        lg.append("session.note", f"{name}-{i}", {"text": str(i)})
    return n


def _hold_lock(repo, taken, release):
    """A process that owns the append lock until told to let go: the slow path, on demand."""
    from ddflow.infra import fsio

    with fsio.file_lock(EventLog(repo, "holder").lock_path, scaled(30)):
        taken.set()
        release.wait(scaled(60))


def _lose_nothing(repo, lock_s: float, held_for_s: float = 0.0):
    """8 processes append 15 events each. With ``held_for_s`` the lock is first taken by
    another process and kept until every writer has started and then ``held_for_s`` longer,
    so each writer really waits on it, as on a loaded machine; ``lock_s`` is the wait each
    writer will accept."""
    n_proc, per = 8, 15
    ctx = mp.get_context()
    taken, release, counter = ctx.Event(), ctx.Event(), ctx.Value("i", 0)
    holder = None
    if held_for_s:
        holder = ctx.Process(target=_hold_lock, args=(repo, taken, release), daemon=True)
        holder.start()
    jobs = [(str(repo), f"w{i}", per, lock_s) for i in range(n_proc)]
    try:
        if holder:
            _wait.wait_until(lambda: taken.is_set() or not holder.is_alive(), 30, what="lock taken")
            assert taken.is_set(), "the holder died before it took the lock"
        with mp.Pool(n_proc, initializer=_init_worker, initargs=(counter,)) as pool:
            pending = pool.map_async(_writer, jobs)
            if holder:
                _wait.wait_until(lambda: counter.value >= n_proc, 30, what="all writers started")
                time.sleep(held_for_s)  # every writer is now past its start, waiting or about to
                release.set()
            pending.get(scaled(120))
    finally:
        release.set()
        if holder:
            holder.join(scaled(30))
    notes = [e for e in EventLog(repo, "reader").read_all() if e.kind == "session.note"]
    assert len(notes) == n_proc * per, "an append was lost under concurrency"
    lamports = [e.lamport for e in notes]
    assert len(set(lamports)) == len(lamports), "lamport values must be unique"


def test_concurrent_writers_lose_nothing(repo):
    """The property the whole lock exists for, tested with real processes. The wait each
    writer accepts is generous: under load a writer queues behind seven others, and the
    default 30 s timed out in the ci gate (B83a8b93429)."""
    _lose_nothing(repo, scaled(30))


def test_a_slow_lock_wait_is_not_a_failed_append(repo, monkeypatch):
    """The same run forced onto the slow path: the lock is busy for 3 s while each writer
    accepts a wait of scaled(1.0) (64 s: the load factor is capped at 16). Unscaled (the old fixed timeout) it times out."""
    _wait.pretend_saturated(monkeypatch)
    _lose_nothing(repo, scaled(1.0), held_for_s=3.0)


def test_canonical_json_is_stable():
    a = canonical({"b": 1, "a": [3, 2]})
    b = canonical({"a": [3, 2], "b": 1})
    assert a == b, "hash input must not depend on key insertion order"

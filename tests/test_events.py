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


def _writer(args):
    root, name, n, lock_s = args
    lg = EventLog(Path(root), name, lock_timeout_s=lock_s)
    for i in range(n):
        lg.append("session.note", f"{name}-{i}", {"text": str(i)})
    return n


def _hold_lock(repo, started, hold_s):
    """A process that owns the append lock until told to let go: the slow path, on demand."""
    from ddflow.infra import fsio

    with fsio.file_lock(EventLog(repo, "holder").lock_path, 30):
        started.set()
        time.sleep(hold_s)  # the lock is held for a fixed time: that IS the slow path


def _lose_nothing(repo, lock_s: float, held_for_s: float = 0.0):
    """8 processes append 15 events each. With ``held_for_s`` the lock is first taken by
    another process for that long, so every writer waits on it the way it would on a loaded
    machine; ``lock_s`` is the wait each writer will accept."""
    n_proc, per = 8, 15
    ctx = mp.get_context()
    started = ctx.Event()
    holder = None
    if held_for_s:
        holder = ctx.Process(target=_hold_lock, args=(repo, started, held_for_s))
        holder.start()
        assert started.wait(30), "the holder never took the lock"
    with mp.Pool(n_proc) as pool:
        pool.map(_writer, [(str(repo), f"w{i}", per, lock_s) for i in range(n_proc)])
    if holder:
        holder.join(30)
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
    """The same run forced onto the slow path: the lock is busy for 2 s while each writer
    accepts a wait of scaled(0.5). Unscaled (the old fixed timeout) it times out."""
    monkeypatch.setattr(_wait, "slow_factor", lambda: 8.0)
    _lose_nothing(repo, scaled(0.5), held_for_s=2.0)


def test_canonical_json_is_stable():
    a = canonical({"b": 1, "a": [3, 2]})
    b = canonical({"a": [3, 2], "b": 1})
    assert a == b, "hash input must not depend on key insertion order"

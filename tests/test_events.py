"""The log is the source of truth, so its guarantees are the ones that matter most."""

import json
import multiprocessing as mp
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orchard.events import Event, EventLog, canonical


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
    root, name, n = args
    lg = EventLog(Path(root), name)
    for i in range(n):
        lg.append("session.note", f"{name}-{i}", {"text": str(i)})
    return n


def test_concurrent_writers_lose_nothing(repo):
    """The property the whole lock exists for, tested with real processes."""
    n_proc, per = 8, 15
    with mp.Pool(n_proc) as pool:
        pool.map(_writer, [(str(repo), f"w{i}", per) for i in range(n_proc)])
    events = EventLog(repo, "reader").read_all()
    notes = [e for e in events if e.kind == "session.note"]
    assert len(notes) == n_proc * per, "an append was lost under concurrency"
    lamports = [e.lamport for e in notes]
    assert len(set(lamports)) == len(lamports), "lamport values must be unique"


def test_canonical_json_is_stable():
    a = canonical({"b": 1, "a": [3, 2]})
    b = canonical({"a": [3, 2], "b": 1})
    assert a == b, "hash input must not depend on key insertion order"

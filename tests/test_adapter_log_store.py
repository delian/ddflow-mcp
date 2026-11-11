"""The `LogStore` port: the default adapter's behaviour and the fake's (CONFORMANCE).

The cases run against the local jsonl adapter (over a scratch dir) and the in-memory fake
once each (`test_ports_contract` does the second run). Appends use a known event kind so the
default adapter exercises the real `EventLog.append` write path; the storage-level assertions
(ordering, offset, shards, snapshot, compaction, blobs) are what they must both satisfy.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from ddflow.adapters.fakes import FAKES
from ddflow.adapters.fakes.log_store import FakeLogStore
from ddflow.adapters.log_store import LogStore
from ddflow.core import blobref as B
from ddflow.infra.log import EventLog
from ddflow.services.log_store import LocalLogStore

#: A kind the domain log accepts (`known_kinds`): it writes one event per append with no
#: stamp once `stamp` is off, so the adapter and the fake record the same stream.
KIND = "task.added"


def _subjects(events) -> list[str]:
    return [e.subject for e in events]


def log_store_conformance(store: LogStore) -> None:
    """What every `LogStore` does, the local adapter and the in-memory fake alike."""
    e1 = store.append(KIND, "first", {"marker": 1})
    e2 = store.append(KIND, "second", {"marker": 2})
    e3 = store.append(KIND, "third", {"marker": 3})

    # append hands back the record, ordered: the store's monotonic sequence.
    assert e1.lamport < e2.lamport < e3.lamport
    assert [e.kind for e in (e1, e2, e3)] == [KIND, KIND, KIND]
    assert _subjects((e1, e2, e3)) == ["first", "second", "third"]

    # read_from(0) is the whole stream, sorted; read_from(offset) resumes after the offset.
    assert _subjects(store.read_from(0)) == ["first", "second", "third"]
    lamports = [e.lamport for e in store.read_from(0)]
    assert lamports == sorted(lamports)
    assert _subjects(store.read_from(e1.lamport)) == ["second", "third"]
    assert _subjects(store.read_from(e3.lamport)) == []

    # one shard for the one agent the cases write under, named like the local store's
    assert store.shards() == ["agent.jsonl"]

    # a snapshot is a point-in-time copy a later append does not reach back into
    snap = store.snapshot()
    assert _subjects(snap) == ["first", "second", "third"]
    store.append(KIND, "fourth", {"marker": 4})
    assert _subjects(snap) == ["first", "second", "third"]
    assert _subjects(store.read_from(0)) == ["first", "second", "third", "fourth"]

    # compaction never changes what the store reads
    before = _subjects(store.read_from(0))
    removed = store.compact()
    assert isinstance(removed, int) and removed >= 0
    assert _subjects(store.read_from(0)) == before

    # blobs are put and got by digest, and a wrong digest is refused
    raw = b"log-store blob"
    ref = B.address(raw)
    assert store.blob_get(ref) is None
    store.blob_put(ref, raw)
    assert store.blob_get(ref) == raw
    assert ref in list(store.blob_refs())
    with pytest.raises(ValueError):
        store.blob_put(B.address(b"other bytes"), raw)


def _real(monkeypatch) -> LocalLogStore:
    return LocalLogStore(Path(tempfile.mkdtemp(prefix="log-store-conf-")), agent_id="agent")


CONFORMANCE = {"LogStore": {"real": _real, "cases": log_store_conformance}}


def test_the_adapter_and_the_fake_satisfy_the_port():
    assert isinstance(LocalLogStore(Path("/tmp/log-store-port")), LogStore)
    assert isinstance(FakeLogStore(), LogStore)
    assert FAKES["LogStore"] is FakeLogStore


def test_replay_the_real_log_through_adapter_and_fake(tmp_path):
    """The default adapter reads the current jsonl shards byte-for-byte unchanged, and the
    fake reproduces the same stream: replaying one real log through both agrees."""
    real = EventLog(tmp_path, "probe")
    real.stamp = False  # the storage write; the SAME write the adapter issues
    expected = [f"s{i}" for i in range(5)]
    for i in range(5):
        real.append(KIND, expected[i], {"marker": i})

    store = LocalLogStore(tmp_path, agent_id="probe")
    assert _subjects(store.read_from(0)) == expected

    fake = FakeLogStore()
    for i in range(5):
        fake.append(KIND, expected[i], {"marker": i})
    assert _subjects(fake.read_from(0)) == expected


def test_fake_supports_multiple_agent_shards_and_compaction():
    f = FakeLogStore()
    f.append("eta.sim", "a", {"marker": 1}, agent="one")
    f.append("eta.sim", "b", {"marker": 2}, agent="two")
    assert f.shards() == ["one.jsonl", "two.jsonl"]
    # events from both shards are in one sorted stream; compaction merges them
    assert [e.agent for e in f.read_from(0)] == ["one", "two"]
    assert f.compact() == 2
    assert f.shards() == ["agent.jsonl"]
    assert _subjects(f.read_from(0)) == ["a", "b"]


def test_fake_blob_ref_is_checked():
    f = FakeLogStore()
    ref = B.address(b"body")
    f.blob_put(ref, b"body")
    with pytest.raises(ValueError):
        f.blob_put(B.address(b"other"), b"body")
    with pytest.raises(ValueError):
        f.blob_put("not-a-ref", b"body")
    assert f.blob_get(ref) == b"body"


def test_default_adapter_writes_real_jsonl_shards(tmp_path):
    store = LocalLogStore(tmp_path, agent_id="w")
    store.append(KIND, "x", {"marker": 9})
    shard = tmp_path / ".ddflow" / "events" / "w.jsonl"
    assert shard.is_file(), "the default adapter writes the jsonl shard, not an abstraction"
    assert _subjects(store.read_from(0)) == ["x"]

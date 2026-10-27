"""The store's incremental rebuild, pinned by the falsifier at the top.

The disposability contract -- the index is a cache, never a record -- is what
makes the incremental projector in ``ddflow/infra/store.py`` a legitimate
shortcut instead of a second implementation of ``fold``. These tests fix BOTH
sides of that contract:

* ``test_dropping_the_db_changes_nothing``: deleting the database and rebuilding
  recreates every row a reader can see. Extended over append-only deltas (the
  case the incremental path short-cuts) so a drop lands on a database the
  incremental projector wrote, not only on a fresh one.

* ``test_incremental_rebuild_matches_a_fresh_rebuild``: the incremental path's
  whole reason to exist -- a rebuild that only reconciles the appended events
  must leave the same rows and the same meta as deleting the database first.
"""

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ddflow.infra.store import Store, _role_seq, _seq_role
from ddflow.services import sessions as session

#: The tables a reader can see, in row order: what "the same rows" means.
BASE_TABLES = (
    "items",
    "lessons",
    "research",
    "decisions",
    "memories",
    "bugs",
    "prompts",
    "records",
    "cadences",
    "similar_docs",
)
#: Meta rows that are provenance, not projection: `built_at` legitimately differs.
_SKIP_META = {"built_at"}


@pytest.fixture
def store(repo, cfg):
    return Store(repo, cfg)


def _seed(log, cfg):
    """The events both the initial build and the deltas extend."""
    log.append("lesson.recorded", "L1", {"title": "Alpha rule", "rule": "do the alpha"})
    log.append("phase.added", "P1", {"title": "phase one"})
    log.append("task.added", "P1.T1", {"parent": "P1", "title": "login", "needs": []})
    return session.start(log, cfg, model="m")


def _grow(log, cfg, sid):
    """The append-only deltas: updates AND removals, the two things an incremental
    projector gets wrong first."""
    log.append("lease.acquired", "P1.T1", {"holder": "a", "at": 1.0, "ttl_s": 60})
    session.prompt(log, cfg, sid, "Please build the login page")
    log.append("item.completed", "P1.T1", {"sha": "deadbeef"})
    log.append("memory.recorded", "M1", {"text": "holds", "tags": ["x"]})
    log.append("memory.forgotten", "M1", {"reason": "no longer true"})
    log.append(
        "decision.recorded",
        "D1",
        {"title": "t", "context": "c", "decision": "d", "by": "o"},
    )
    log.append("research.recorded", "R1", {"question": "q?", "claim": "c", "verdict": "UNKNOWN"})


def _dump(store):
    """Every row a reader can see, plus the comparable meta: the falsifier's oracle."""
    with sqlite3.connect(store.path) as con:
        rows = {}
        for t in BASE_TABLES:
            n_cols = len(con.execute(f"pragma table_info({t})").fetchall())
            order = ", ".join(str(i) for i in range(1, n_cols + 1))
            rows[t] = [tuple(r) for r in con.execute(f"select * from {t} order by {order}")]
        meta = {k: v for k, v in con.execute("select k, v from meta") if k not in _SKIP_META}
    return rows, meta


def test_dropping_the_db_changes_nothing(log, cfg, store):
    """The disposability contract, over append-only deltas.

    The database removed AFTER the deltas were projected must rebuild to the
    same rows as the one the deltas landed on incrementally -- the extension of
    the original falsifier to the path the incremental projector owns.
    """
    sid = _seed(log, cfg)
    store.rebuild(log)
    hits_before = store.search("lessons", "alpha", 3)
    _grow(log, cfg, sid)
    incremental = store.rebuild(log)  # the deltas land on the incremental projector
    after_deltas = _dump(store)
    store.path.unlink()
    after = store.rebuild(log)
    assert set(incremental.items) == set(after.items)
    assert store.search("lessons", "alpha", 3) == hits_before
    assert _dump(store) == after_deltas


def test_rebuild_records_its_shard_verification_marks(log, cfg, store):
    """A built (or reconciled) index must record the per-shard consumed offsets and
    their digests -- the marks the incremental replay is not allowed to act
    without (the 'never unverified incremental' doctrine). A bogus mark, or one
    that does not digest the consumed bytes, is not a mark."""
    _seed(log, cfg)
    store.rebuild(log)
    with sqlite3.connect(store.path) as con:
        meta = dict(con.execute("select k, v from meta"))
    mark = json.loads(meta["mark"])
    digests = json.loads(meta["shard_digests"])
    shards = sorted(store.root.glob(".ddflow/events/*.jsonl"))
    assert set(mark) == set(digests) == {p.name for p in shards}
    for p in shards:
        data = p.read_bytes()
        size_s, slash, hexed = str(digests[p.name]).partition("/")
        assert slash and int(size_s) == len(data) == mark[p.name]
        assert hexed == hashlib.sha256(data).hexdigest()


def test_incremental_rebuild_matches_a_fresh_rebuild(log, cfg, store, monkeypatch):
    """Reconciling the appended deltas in place leaves the SAME rows and the SAME
    meta as a rebuild from nothing -- the criterion that must hold before a
    full rebuild may be skipped. The knob is pinned ON: an exported
    `DDFLOW_STORE_INCREMENTAL=0` must not silently demote the pin to two full
    rebuilds, which would pass while the incremental mis-reconciles."""
    monkeypatch.setenv("DDFLOW_STORE_INCREMENTAL", "1")
    sid = _seed(log, cfg)
    store.rebuild(log)
    _grow(log, cfg, sid)
    incremental = store.rebuild(log)
    kept = _dump(store)
    store.path.unlink()
    dropped = store.rebuild(log)
    assert set(incremental.items) == set(dropped.items)
    assert _dump(store) == kept


def test_incremental_reconcile_can_be_disabled(log, cfg, store, monkeypatch):
    """The knob, in BOTH directions. ON (its default) reconciles the appended
    deltas in place; OFF a rebuild is the old full path, and the result is the
    same rows anyway. A store that ignored the knob in either direction cannot
    pass: not one that still reconciles while disabled, and not one that never
    reconciles while enabled."""
    original = Store._reconcile
    calls = []

    def _spy(self, *a, **k):
        calls.append(True)
        return original(self, *a, **k)

    monkeypatch.delenv("DDFLOW_STORE_INCREMENTAL", raising=False)
    monkeypatch.setattr(Store, "_reconcile", _spy)
    built: list[bool] = []
    original = Store._build_into

    def _spy_build(self, tmp, state, events, fingerprint, marks):
        built.append(True)
        return original(self, tmp, state, events, fingerprint, marks)

    monkeypatch.setattr(Store, "_build_into", _spy_build)
    sid = _seed(log, cfg)
    store.rebuild(log)
    calls.clear()
    built.clear()
    _grow(log, cfg, sid)
    store.rebuild(log)
    assert calls, "the appended deltas did not go through _reconcile while enabled"
    assert not built, (
        "the enabled rebuild re-wrote the index; its result did not come from _reconcile"
    )

    monkeypatch.setenv("DDFLOW_STORE_INCREMENTAL", "0")
    calls.clear()

    def _boom(self, *a, **k):
        calls.append(True)
        raise AssertionError("_reconcile called while the knob disables it")

    monkeypatch.setattr(Store, "_reconcile", _boom)
    _grow(log, cfg, sid)
    store.rebuild(log)
    assert not calls
    kept = _dump(store)
    store.path.unlink()
    store.rebuild(log)
    assert _dump(store) == kept


def test_a_rewritten_shard_prefix_is_still_caught(log, cfg, store, monkeypatch):
    """The verification half of the doctrine: a shard whose consumed prefix was
    rewritten in place under the mark's nose (same size, growth after) is not
    reconciled -- the digest mismatch forces the rebuild. The knob is pinned ON:
    a disabled knob would park this pin on the full-rebuild path, which performs
    no verification at all."""
    monkeypatch.setenv("DDFLOW_STORE_INCREMENTAL", "1")
    sid = _seed(log, cfg)
    store.rebuild(log)
    for p in sorted(store.root.glob(".ddflow/events/*.jsonl")):
        p.write_bytes(p.read_bytes().replace(b'"phase one"', b'"phase two"'))
    log.append("lesson.recorded", "L2", {"title": "Beta", "rule": "beta"})
    _grow(log, cfg, sid)
    store.rebuild(log)
    kept = _dump(store)
    store.path.unlink()
    store.rebuild(log)
    assert _dump(store) == kept


def test_prompt_note_and_summary_seqs_are_disjoint_and_invertible():
    """A prompt, a note and a summary can never collide on the `prompts` primary
    key however many entries one session holds: each role's seq space is disjoint
    and its letter maps it back, so two FTS rows can never sit under one base row
    (the shifted-10,000 scheme a 20,001st entry overflowed)."""
    for role in ("prompt", "note", "summary"):
        for seq in range(1000):
            assert _seq_role(role, _role_seq(role, seq)) == seq
    taken = [_role_seq("prompt", s) for s in range(1000)]
    taken += [_role_seq("note", s) for s in range(1000)]
    taken += [_role_seq("summary", 0)]
    assert len(taken) == len(set(taken))


def test_a_disabled_knob_still_records_marks_that_digest_the_shards(log, cfg, store, monkeypatch):
    """The knob off is not a licence to skip the marks: a knob-off rebuild still
    records per-shard consumed bytes and their digests in the index it publishes,
    taken before the events are read -- the pin extends to the non-incremental
    path the marks licence."""
    monkeypatch.setenv("DDFLOW_STORE_INCREMENTAL", "0")
    _seed(log, cfg)
    store.rebuild(log)
    with sqlite3.connect(store.path) as con:
        digests = json.loads(dict(con.execute("select k, v from meta"))["shard_digests"])
    for p in sorted(store.root.glob(".ddflow/events/*.jsonl")):
        size_s, slash, hexed = str(digests[p.name]).partition("/")
        assert slash and int(size_s) <= len(p.read_bytes())
        assert hexed == hashlib.sha256(p.read_bytes()[: int(size_s)]).hexdigest()

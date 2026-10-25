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

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ddflow.infra.store import Store
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
        rows = {
            t: [tuple(r) for r in con.execute(f"select * from {t}").fetchall()] for t in BASE_TABLES
        }
        meta = {k: v for k, v in con.execute("select k, v from meta") if k not in _SKIP_META}
    return rows, meta


def test_dropping_the_db_changes_nothing(log, cfg, store):
    """The disposability contract, over append-only deltas.

    The database removed AFTER the deltas were projected must rebuild to the
    same rows as the one the deltas landed on incrementally -- the extension of
    the original falsifier to the path the incremental projector owns.
    """
    sid = _seed(log, cfg)
    hits_before = store.search("lessons", "alpha", 3)
    store.rebuild(log)
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
    without (the 'never unverified incremental' doctrine)."""
    _seed(log, cfg)
    store.rebuild(log)
    with sqlite3.connect(store.path) as con:
        meta = dict(con.execute("select k, v from meta"))
    assert meta.get("mark")
    assert meta.get("shard_digests")


def test_incremental_rebuild_matches_a_fresh_rebuild(log, cfg, store):
    """Reconciling the appended deltas in place leaves the SAME rows and the SAME
    meta as a rebuild from nothing -- the criterion that must hold before a
    full rebuild may be skipped."""
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
    """The knob: with the incremental projector off, a rebuild is the old full
    path, and the result is the same rows anyway."""
    monkeypatch.setenv("DDFLOW_STORE_INCREMENTAL", "0")
    sid = _seed(log, cfg)
    store.rebuild(log)
    _grow(log, cfg, sid)
    store.rebuild(log)
    kept = _dump(store)
    store.path.unlink()
    store.rebuild(log)
    assert _dump(store) == kept


def test_a_rewritten_shard_prefix_is_still_caught(log, cfg, store):
    """The verification half of the doctrine: a shard whose consumed prefix was
    rewritten in place under the mark's nose (same size, growth after) is not
    reconciled -- the digest mismatch forces the rebuild."""
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

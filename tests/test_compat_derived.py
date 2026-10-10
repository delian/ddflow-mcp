"""D-compat: derived and local stores are keyed on what built them. The index and the read
snapshot carry a code fingerprint in their name, so checkouts on different code never
rebuild each other's; a local store written by a NEWER ddflow is ignored when derived, and
the user-level quota file is read for the fields this version knows and written back with
the rest kept. The generic LocalStore still refuses newer USER data (B-uni-compat-derived)."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ddflow.infra import localstore as LS
from ddflow.infra import log as L
from ddflow.infra import store as S
from ddflow.services import quota as Q


def _store(repo, cfg, fp: str) -> S.Store:
    """A Store as code with fingerprint ``fp`` would build it."""
    orig = S.code_fingerprint
    S.code_fingerprint = lambda: fp  # type: ignore[assignment]
    try:
        return S.Store(repo, cfg)
    finally:
        S.code_fingerprint = orig  # type: ignore[assignment]


def test_the_fingerprint_is_stable_and_names_the_index(repo, cfg):
    fp = S.code_fingerprint()
    assert len(fp) == 16 and fp == S.code_fingerprint()
    assert S.Store(repo, cfg).path.name == f"index.db-{fp}"


def test_the_fingerprint_follows_the_schema_and_the_format(monkeypatch):
    before = S.code_fingerprint()
    S.code_fingerprint.cache_clear()
    monkeypatch.setattr(S, "SCHEMA", S.SCHEMA + 1)
    try:
        assert S.code_fingerprint() != before
    finally:
        monkeypatch.undo()
        S.code_fingerprint.cache_clear()
    assert S.code_fingerprint() == before


def test_two_fingerprints_keep_two_indexes_and_do_not_rebuild_each_other(log, repo, cfg):
    log.append("lesson.recorded", "L1", {"title": "Alpha", "rule": "alpha"})
    a, b = _store(repo, cfg, "a" * 16), _store(repo, cfg, "b" * 16)
    assert a.path != b.path
    a.rebuild(log)
    b.rebuild(log)
    assert not a.stale(log) and not b.stale(log)
    built_a = a.path.stat().st_mtime_ns
    b.rebuild(log)  # b's code rebuilding does not touch a's index
    assert a.path.stat().st_mtime_ns == built_a and not a.stale(log)


def test_an_old_index_of_other_code_is_collected_on_rebuild(log, repo, cfg):
    log.append("lesson.recorded", "L1", {"title": "Alpha", "rule": "alpha"})
    me = _store(repo, cfg, "c" * 16)
    d = me.path.parent
    d.mkdir(parents=True, exist_ok=True)
    old = d / ("index.db-" + "d" * 16)
    old_wal = d / (old.name + "-wal")
    young = d / ("index.db-" + "e" * 16)
    legacy = d / "index.db"
    unrelated = d / "index.dbx"
    for f in (old, old_wal, young, legacy, unrelated):
        f.write_text("x")
    ago = time.time() - (S.STALE_INDEX_DAYS + 1) * 86400
    for f in (old, old_wal, legacy, unrelated):
        os.utime(f, (ago, ago))
    me.rebuild(log)
    assert not old.exists() and not old_wal.exists() and not legacy.exists()
    assert young.exists() and unrelated.exists() and me.path.exists()


def test_the_read_snapshot_is_per_version(repo, monkeypatch):
    log = L.EventLog(repo / ".ddflow" / "events")
    here = log._snapshot_path()
    monkeypatch.setattr(L, "running_version", lambda: "9.9.9")
    there = log._snapshot_path()
    assert here != there and here.parent == there.parent
    assert here.name.startswith("read-snapshot-") and here.suffix == ".bin"


def test_a_stale_snapshot_is_collected(repo):
    log = L.EventLog(repo / ".ddflow" / "events")
    mine = log._snapshot_path()
    d = mine.parent
    d.mkdir(parents=True, exist_ok=True)
    other = d / ("read-snapshot-" + "f" * 16 + ".bin")
    legacy = d / L.SNAPSHOT_FILE
    fresh = d / ("read-snapshot-" + "0" * 16 + ".bin")
    for f in (other, legacy, fresh):
        f.write_bytes(b"x")
    ago = time.time() - (L.SNAPSHOT_STALE_DAYS + 1) * 86400
    for f in (other, legacy):
        os.utime(f, (ago, ago))
    log._collect_stale_snapshots(mine)
    assert not other.exists() and not legacy.exists() and fresh.exists()


# -- local stores ---------------------------------------------------------------------


def _newer(store: LS.LocalStore, name: str, data) -> None:
    store.root.mkdir(parents=True, exist_ok=True)
    store.path(name).write_text(
        json.dumps({"schema": 9, "ddflow": "9.9.9", "fmt": 9, "data": data, "future": 1})
    )


def test_a_derived_document_of_a_newer_schema_is_ignored_and_rebuilt(tmp_path):
    store = LS.LocalStore(tmp_path / "local")
    _newer(store, "c.json", {"new": "shape"})
    assert store.read("c.json", derived=True, default="gone") == "gone"
    store.write("c.json", {"mine": 1}, derived=True)
    assert store.read("c.json") == {"mine": 1}


def test_user_data_of_a_newer_schema_is_still_refused(tmp_path):
    store = LS.LocalStore(tmp_path / "local")
    _newer(store, "u.json", {"new": "shape"})
    with pytest.raises(LS.fsio.NewerContent):
        store.read("u.json")
    with pytest.raises(LS.fsio.NewerContent):
        store.write("u.json", {})


def test_a_queue_of_a_newer_schema_starts_over(tmp_path):
    store = LS.LocalStore(tmp_path / "local")
    _newer(store, "q.json", {"k": {}})
    assert store.queue_pending("q.json") == {}
    assert store.queue_put("q.json", "k", {"p": 1}) == 1


# -- the quota store ------------------------------------------------------------------


@pytest.fixture
def quotas(tmp_path):
    return tmp_path / "quotas.json"


def _future_doc() -> dict:
    return {
        "version": 7,
        "future_top": {"x": 1},
        "profiles": {
            "agent:h/a": {
                "subject": "agent:h/a",
                "unlimited": True,
                "declared_by": "operator",
                "at": "2026-01-01T00:00:00Z",
                "future_field": "kept",
                "windows": [],
            }
        },
    }


def test_a_newer_quota_store_is_read_for_what_this_version_knows(quotas):
    quotas.write_text(json.dumps(_future_doc()))
    assert Q.load(quotas)["agent:h/a"].unlimited is True


def test_writing_a_newer_quota_store_keeps_what_this_version_does_not_know(quotas):
    quotas.write_text(json.dumps(_future_doc()))
    Q.declare(Q.Profile(subject="llm:http://x", unlimited=True), quotas)
    doc = json.loads(quotas.read_text())
    assert doc["version"] == 7 and doc["future_top"] == {"x": 1}
    assert doc["profiles"]["agent:h/a"]["future_field"] == "kept"
    assert "llm:http://x" in doc["profiles"]


def test_an_unversioned_quota_store_is_still_refused(quotas):
    quotas.write_text(json.dumps({"profiles": {}}))
    with pytest.raises(Q.QuotaError):
        Q.load(quotas)


def test_window_level_unknown_keys_are_kept_too(quotas):
    doc = _future_doc()
    doc["profiles"]["agent:h/a"] = {
        "subject": "agent:h/a",
        "declared_by": "operator",
        "at": "2026-01-01T00:00:00Z",
        "windows": [{"window": "day", "limit": 5, "unit": "usd", "burst": 3}],
    }
    quotas.write_text(json.dumps(doc))
    Q.declare(Q.Profile(subject="llm:http://x", unlimited=True), quotas)
    saved = json.loads(quotas.read_text())["profiles"]["agent:h/a"]["windows"]
    assert saved[0]["burst"] == 3 and saved[0]["limit"] == 5


def test_an_index_in_use_is_touched_so_it_is_not_taken_for_abandoned(log, repo, cfg):
    log.append("lesson.recorded", "L1", {"title": "Alpha", "rule": "alpha"})
    st = S.Store(repo, cfg)
    st.rebuild(log)
    ago = time.time() - (S.STALE_INDEX_DAYS + 1) * 86400
    os.utime(st.path, (ago, ago))
    assert not st.stale(log)  # a reader asks, finds it current ...
    assert time.time() - st.path.stat().st_mtime < 60  # ... and marks it used


def test_forgetting_a_subject_of_a_newer_store_removes_it_and_keeps_the_rest(quotas):
    quotas.write_text(json.dumps(_future_doc()))
    Q.forget("agent:h/a", "operator", quotas)
    doc = json.loads(quotas.read_text())
    assert doc["profiles"] == {} and doc["future_top"] == {"x": 1} and doc["version"] == 7


def test_a_snapshot_in_use_is_touched(repo, monkeypatch):
    log = L.EventLog(repo / ".ddflow" / "events")
    path = log._snapshot_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a snapshot\n")
    ago = time.time() - (L.SNAPSHOT_STALE_DAYS + 1) * 86400
    os.utime(path, (ago, ago))
    monkeypatch.setattr(L.EventLog, "_snapshot_enabled", lambda self: True)
    log._load_snapshot()  # unreadable header: not touched
    assert path.stat().st_mtime < time.time() - 86400
    head = {
        "format": L.SNAPSHOT_FORMAT,
        "version": L.running_version(),
        "fields": list(L._EVENT_FIELDS),
        "parser": L._parser_stamp(),
        "size": 2,
        "sha256": L.D.content_digest(b"{}"),
    }
    import marshal

    payload = marshal.dumps({})
    head["size"], head["sha256"] = len(payload), L.D.content_digest(payload)
    path.write_bytes(json.dumps(head).encode() + b"\n" + payload)
    os.utime(path, (ago, ago))
    L._SNAPSHOTS.pop(log.dir, None)
    log._load_snapshot()
    assert time.time() - path.stat().st_mtime < 60

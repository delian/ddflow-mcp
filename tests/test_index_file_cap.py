"""PS-index-cap (bug Bece55b8a77, r-derived-cache-bounded): the index is keyed on an explicit
schema/fold revision, and the number of index files is capped with an env kill switch."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ddflow.infra import store as S


@pytest.fixture(autouse=True)
def _clean_knobs(monkeypatch):
    for k in ("GC", "MAX_FILES", "MAX_MB", "KEY"):
        monkeypatch.delenv(f"DDFLOW_INDEX_{k}", raising=False)


def _store(repo, cfg, fp: str) -> S.Store:
    orig = S.code_fingerprint
    S.code_fingerprint = lambda: fp  # type: ignore[assignment]
    try:
        return S.Store(repo, cfg)
    finally:
        S.code_fingerprint = orig  # type: ignore[assignment]


def _mains(repo) -> list[Path]:
    return sorted(
        p
        for p in (repo / ".ddflow").glob("index.db-*")
        if p.name[9:].isalnum() and len(p.name) == 25
    )


def _simulate_edits(log, repo, cfg, n: int) -> None:
    for i in range(n):
        s = _store(repo, cfg, f"{i:016x}")
        s.rebuild(log)
        t = time.time() - (n - i)  # distinct, increasing mtimes
        os.utime(s.path, (t, t))


def test_twenty_core_edits_leave_at_most_k_index_files(log, repo, cfg, monkeypatch):
    monkeypatch.delenv("DDFLOW_INDEX_GC", raising=False)
    monkeypatch.delenv("DDFLOW_INDEX_MAX_FILES", raising=False)
    log.append("lesson.recorded", "L1", {"title": "Alpha", "rule": "alpha"})
    _simulate_edits(log, repo, cfg, 20)
    assert len(_mains(repo)) <= S.INDEX_MAX_FILES == 3


def test_the_newest_files_survive_and_oldest_go(log, repo, cfg, monkeypatch):
    monkeypatch.setenv("DDFLOW_INDEX_MAX_FILES", "2")
    log.append("lesson.recorded", "L1", {"title": "Alpha", "rule": "alpha"})
    _simulate_edits(log, repo, cfg, 6)
    assert [p.name[9:] for p in _mains(repo)] == [f"{4:016x}", f"{5:016x}"]


def test_kill_switch_keeps_every_file(log, repo, cfg, monkeypatch):
    monkeypatch.setenv("DDFLOW_INDEX_GC", "0")
    log.append("lesson.recorded", "L1", {"title": "Alpha", "rule": "alpha"})
    _simulate_edits(log, repo, cfg, 6)
    assert len(_mains(repo)) == 6


def test_a_total_size_cap_removes_oldest_first(log, repo, cfg, monkeypatch):
    log.append("lesson.recorded", "L1", {"title": "Alpha", "rule": "alpha"})
    monkeypatch.setenv("DDFLOW_INDEX_GC", "0")
    _simulate_edits(log, repo, cfg, 3)
    monkeypatch.delenv("DDFLOW_INDEX_GC")
    size = _mains(repo)[0].stat().st_size
    monkeypatch.setenv("DDFLOW_INDEX_MAX_MB", str(max(size * 1.5 / 1048576, 1e-6)))
    _store(repo, cfg, f"{3:016x}").rebuild(log)
    assert [p.name[9:] for p in _mains(repo)] == [f"{3:016x}"]


def test_the_key_ignores_source_edits_but_follows_fold_rev(monkeypatch):
    before = S.code_fingerprint()
    S.code_fingerprint.cache_clear()
    monkeypatch.setattr(S, "FOLD_REV", S.FOLD_REV + 1)
    try:
        assert S.code_fingerprint() != before
    finally:
        monkeypatch.undo()
        S.code_fingerprint.cache_clear()
    assert S.code_fingerprint() == before


def test_a_source_edit_does_not_change_the_explicit_key(monkeypatch):
    before = S.code_fingerprint()
    S.code_fingerprint.cache_clear()
    monkeypatch.setattr(Path, "read_bytes", lambda self: b"edited core source")
    try:
        assert S.code_fingerprint() == before
    finally:
        monkeypatch.undo()
        S.code_fingerprint.cache_clear()


def test_source_key_mode_restores_the_source_hash(monkeypatch):
    monkeypatch.setenv("DDFLOW_INDEX_KEY", "source")
    seen = []
    for text in (b"edit one", b"edit two", b"edit one"):
        S.code_fingerprint.cache_clear()
        monkeypatch.setattr(Path, "read_bytes", lambda self, t=text: t)
        seen.append(S.code_fingerprint())
    monkeypatch.undo()
    S.code_fingerprint.cache_clear()
    assert seen[0] != seen[1] and seen[0] == seen[2]


def test_a_newer_file_over_the_size_cap_does_not_outlive_an_older_one(log, repo, cfg, monkeypatch):
    d = repo / ".ddflow"
    d.mkdir(exist_ok=True)
    keep = d / ("index.db-" + "a" * 16)
    new = d / ("index.db-" + "b" * 16)
    old = d / ("index.db-" + "c" * 16)
    for f, size, age in ((keep, 900_000, 0), (new, 200_000, 10), (old, 100_000, 20)):
        f.write_bytes(b"x" * size)
        t = time.time() - age
        os.utime(f, (t, t))
    monkeypatch.setenv("DDFLOW_INDEX_MAX_MB", "1")
    S.collect_over_cap(d, keep.name)
    assert keep.exists() and not new.exists() and not old.exists()


def test_a_bad_cap_value_falls_back_and_orphans_go(log, repo, cfg, monkeypatch):
    log.append("lesson.recorded", "L1", {"title": "Alpha", "rule": "alpha"})
    d = repo / ".ddflow"
    d.mkdir(exist_ok=True)
    orphan = d / ("index.db-" + "e" * 16 + "-wal")
    orphan.write_bytes(b"x")
    for bad in ("nan", "inf", "-3", "junk"):
        monkeypatch.setenv("DDFLOW_INDEX_MAX_FILES", bad)
        monkeypatch.setenv("DDFLOW_INDEX_MAX_MB", bad)
        _simulate_edits(log, repo, cfg, 4)
        assert 1 <= len(_mains(repo)) <= 3
    assert not orphan.exists()

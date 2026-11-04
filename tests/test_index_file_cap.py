"""PS-index-cap (bug Bece55b8a77, r-derived-cache-bounded): the index is keyed on an explicit
schema/fold revision, and the number of index files is capped with an env kill switch."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ddflow.infra import store as S


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
    assert len(_mains(repo)) <= 2


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


def test_source_key_mode_restores_the_source_hash(monkeypatch):
    explicit = S.code_fingerprint()
    S.code_fingerprint.cache_clear()
    monkeypatch.setenv("DDFLOW_INDEX_KEY", "source")
    try:
        assert S.code_fingerprint() != explicit
    finally:
        monkeypatch.undo()
        S.code_fingerprint.cache_clear()

"""Value completion dispatches a kind to its source, keyed by the kind only."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ddflow.services.completion import values as V


def _index(d: Path) -> str:
    (d / ".ddflow").mkdir(parents=True, exist_ok=True)
    p = d / ".ddflow" / "index.db-aaaaaaaa"
    con = sqlite3.connect(p)
    con.executescript("""
    create table items(id text, kind text, title text, state text);
    create table bugs(id text, fixed_at text, invalid_at text);
    """)
    con.executemany(
        "insert into items values(?,?,?,?)",
        [("COMP-a", "task", "t", "open"), ("P-comp", "phase", "p", "open")],
    )
    con.commit()
    con.close()
    return str(p)


def test_id_kind_completes_by_kind(tmp_path):
    index = _index(tmp_path)
    assert V.complete("id:task", "COMP", index=index) == ["COMP-a"]
    assert V.complete("id:phase", "P", index=index) == ["P-comp"]
    assert V.complete("id", "COMP", index=index) == ["COMP-a"]


def test_state_kind(tmp_path):
    index = _index(tmp_path)
    assert V.complete("state", "", index=index) == ["open"]
    assert V.complete("state", "cl", index=index) == []  # prefix-filtered, like kind/agent/gate


def test_kind_kind(tmp_path):
    index = _index(tmp_path)
    assert "bugs" in V.complete("kind", "", index=index)
    assert "phase" in V.complete("kind", "", index=index)


def test_enum_reads_choices():
    assert V.enum(("a", "b")) == ["a", "b"]


def test_none_offers_nothing():
    assert V.complete("none", "x") == []


def test_path_and_dir_complete_paths(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("")
    (tmp_path / "b.txt").write_text("")
    (tmp_path / "proj").mkdir()
    monkeypatch.chdir(tmp_path)
    assert V.complete("path", "a") == ["a.txt"]
    assert V.complete("dir", "proj") == ["proj"]
    assert V.complete("dir", "zzz") == []
    # a path kind offers directories too, so `--globs src/**` can traverse into them
    assert V.complete("path", "proj") == ["proj"]

"""`read_only_rows`: a query on a SQLite file ddflow does not own (another tool's store).

The one way a module outside the index reads such a file, so sqlite3 keeps its importers:
tests/test_arch_deps.py fails a new one. What it promises is that the file is never changed
and that whatever cannot be read reads as no rows.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ddflow.infra.docindex import read_only_rows
from ddflow.services import archdeps as A


def _db(path: Path) -> Path:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE part (id TEXT, n INTEGER)")
    con.execute("INSERT INTO part VALUES ('a', 1), ('b', 2)")
    con.commit()
    con.close()
    return path


def test_it_returns_the_matching_rows(tmp_path: Path) -> None:
    db = _db(tmp_path / "store.db")
    assert read_only_rows(db, "SELECT id FROM part WHERE n > ?", (1,)) == [("b",)]


def test_a_path_a_uri_would_misread_is_still_the_file(tmp_path: Path) -> None:
    db = _db(tmp_path / "a?b#c.db")
    assert read_only_rows(db, "SELECT count(*) FROM part") == [(2,)]


def test_a_missing_file_is_no_rows_and_is_not_created(tmp_path: Path) -> None:
    db = tmp_path / "absent.db"
    assert read_only_rows(db, "SELECT 1") == []
    assert not db.exists()


def test_a_file_that_is_not_a_database_is_no_rows(tmp_path: Path) -> None:
    db = tmp_path / "notes.db"
    db.write_text("this is not sqlite, and long enough to read a header from\n" * 40)
    assert read_only_rows(db, "SELECT * FROM part") == []


def test_a_wrong_schema_is_no_rows(tmp_path: Path) -> None:
    db = _db(tmp_path / "store.db")
    assert read_only_rows(db, "SELECT * FROM absent_table") == []


def test_a_path_that_cannot_be_resolved_is_no_rows(tmp_path: Path) -> None:
    loop = tmp_path / "loop.db"
    loop.symlink_to(loop)
    assert read_only_rows(loop, "SELECT 1") == []


def test_a_write_is_refused_and_the_file_keeps_its_rows(tmp_path: Path) -> None:
    db = _db(tmp_path / "store.db")
    assert read_only_rows(db, "DELETE FROM part") == []
    assert read_only_rows(db, "SELECT count(*) FROM part") == [(2,)]


def test_the_approvals_harness_reaches_kilo_through_it_not_through_sqlite3() -> None:
    root = Path(__file__).resolve().parents[1]
    sqlite = next(f for f in A.scan(root).families if f.family == "sqlite3")
    assert "ddflow.services.approvals.harness" not in sqlite.importers

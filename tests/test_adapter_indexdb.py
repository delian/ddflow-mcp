"""The `IndexStore` port: the default adapter and the in-memory fake (CONFORMANCE)."""

from __future__ import annotations

from pathlib import Path

from ddflow.adapters.fakes import FAKES
from ddflow.adapters.fakes.indexdb import FakeIndexStore
from ddflow.adapters.indexdb import DEFAULT, IndexStore, SqliteIndexStore


def _schema(con) -> str:
    """The schema version `migrate` recorded, or "" before it ran (or the table exists not)."""
    made = con.execute(
        "select 1 from sqlite_master where type='table' and name='meta'"
    ).fetchone()
    if not made:
        return ""
    row = con.execute("select v from meta where k='schema'").fetchone()
    return row["v"] if row else ""


def indexdb_conformance(store: IndexStore) -> None:
    """What every `IndexStore` does, the disk adapter and the in-memory fake alike."""
    root = Path("/repo")
    assert store.filename(root, "abc123") == root / ".ddflow" / "index.db-abc123"

    # pragmas are set by `connect` (the fake's are the in-memory equivalents).
    con = store.connect(":memory:")
    try:
        assert _schema(con) == "", "a fresh connection starts unversioned"
        store.migrate(con, 11)
        assert _schema(con) == "11"
        # `Row` rows are addressable by name: the projection's own access pattern.
        store.migrate(con, 42)
        assert _schema(con) == "42"
    finally:
        con.close()


def _real(monkeypatch) -> SqliteIndexStore:
    """The default adapter; `connect(":memory:")` exercises the same cases with no disk."""
    return SqliteIndexStore()


CONFORMANCE = {"IndexStore": {"real": _real, "cases": indexdb_conformance}}


def test_the_adapter_and_the_fake_satisfy_the_port():
    assert isinstance(SqliteIndexStore(), IndexStore)
    assert isinstance(FakeIndexStore(), IndexStore)
    assert FAKES["IndexStore"] is FakeIndexStore
    assert isinstance(DEFAULT, SqliteIndexStore)


def test_default_adapter_writes_on_disk(tmp_path):
    store = SqliteIndexStore()
    p = store.filename(tmp_path, "abc123")
    con = store.connect(p)
    try:
        mode = con.execute("pragma journal_mode").fetchone()[0]
        assert mode.lower() == "wal", "the default adapter sets WAL, not the default journal"
        store.migrate(con, 7)
    finally:
        con.close()
    assert p.is_file(), "the default adapter persists the index on disk"


def test_fake_connect_is_in_memory(tmp_path):
    store = FakeIndexStore()
    con = store.connect(tmp_path / "never" / "created")
    try:
        mode = con.execute("pragma journal_mode").fetchone()[0]
        assert mode.lower() == "memory", "the fake opens :memory:, not a file"
    finally:
        con.close()
    assert not (tmp_path / "never").exists(), "the fake must not create the requested path"


def test_disk_and_fake_connections_share_the_migration_seam():
    """A projection moved onto the port reads the same schema version from either."""
    for store in (SqliteIndexStore(), FakeIndexStore()):
        con = store.connect(":memory:")
        try:
            store.migrate(con, 5)
            assert _schema(con) == "5"
        finally:
            con.close()

"""`FakeIndexStore`: an in-memory `IndexStore` that needs no tmpdir.

Every `connect` opens a fresh `:memory:` database, so tests run without touching a disk.
`sqlite3` stays in `ddflow.adapters.indexdb` (the `sqlite-home` contract); this module
imports it not, reaching the in-memory connection through the home's `connect_memory`.
"""

from __future__ import annotations

from pathlib import Path

from ddflow.adapters.indexdb import Connection, SqliteIndexStore, connect_memory

from . import fake


@fake
class FakeIndexStore(SqliteIndexStore):
    """An `IndexStore` whose connections are in-memory databases, one per path."""

    def connect(self, path: Path | str = ":memory:") -> Connection:
        # One shared-cache in-memory database per path, keyed to this instance, so a
        # reconnect to a path sees the same rows (like the default reopening its file)
        # instead of a fresh empty database with no tmpdir.
        con = connect_memory(name=f"{id(self)}:{path}")
        self.pragmas(con)
        return con

    def pragmas(self, con: Connection) -> None:
        # WAL and NORMAL are disk durability settings; in memory they are pointless.
        con.execute("pragma journal_mode=MEMORY")
        con.execute("pragma synchronous=OFF")
        con.execute("pragma busy_timeout=30000")

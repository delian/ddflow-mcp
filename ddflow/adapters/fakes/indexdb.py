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
    """An `IndexStore` whose connections are `:memory:` databases."""

    def connect(self, path: Path | str = ":memory:") -> Connection:
        # The argument is ignored: an in-memory database has no path to open.
        con = connect_memory()
        self.pragmas(con)
        return con

    def pragmas(self, con: Connection) -> None:
        # WAL and NORMAL are disk durability settings; in memory they are pointless.
        con.execute("pragma journal_mode=MEMORY")
        con.execute("pragma synchronous=OFF")
        con.execute("pragma busy_timeout=30000")

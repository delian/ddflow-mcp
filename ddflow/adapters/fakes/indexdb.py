"""`FakeIndexStore`: an in-memory `IndexStore` that needs no tmpdir.

Each `connect` opens an in-memory database, so tests run without touching a disk.
`sqlite3` stays in `ddflow.adapters.indexdb` (the `sqlite-home` contract); this module
imports it not, reaching the in-memory connection through the home's `connect_memory`.
"""

from __future__ import annotations

from pathlib import Path

from ddflow.adapters.indexdb import Connection, SqliteIndexStore, connect_memory, running

from . import fake


@fake
class FakeIndexStore(SqliteIndexStore):
    """An `IndexStore` whose databases are in-memory, one per path, held for its lifetime."""

    def __init__(self) -> None:
        #: path -> that path's one in-memory database, kept open so a reconnect to the
        #: same path sees the same rows. An in-memory database is not a file: closing
        #: a connection drops it (as sqlite3's own `:memory:` does), so a `close()`
        #: forgets the rows and a later `connect` starts a fresh database.
        self._dbs: dict[str, Connection] = {}

    def connect(self, path: Path | str = ":memory:") -> Connection:
        key = str(path)
        con = self._dbs.get(key)
        if con is None or not running(con):
            con = connect_memory()
            self.pragmas(con)
            self._dbs[key] = con
        return con

    def pragmas(self, con: Connection) -> None:
        # WAL and NORMAL are disk durability settings; in memory they are pointless.
        con.execute("pragma journal_mode=MEMORY")
        con.execute("pragma synchronous=OFF")
        con.execute("pragma busy_timeout=30000")

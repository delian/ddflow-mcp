"""`FakeIndexStore`: an in-memory `IndexStore` that needs no tmpdir.

Each `connect` opens an in-memory database, so tests run without touching a disk.
`sqlite3` stays in `ddflow.adapters.indexdb` (the `sqlite-home` contract); this module
imports it not, reaching the in-memory connection through the home's `connect_memory`.
"""

from __future__ import annotations

from pathlib import Path

from ddflow.adapters.indexdb import Connection, SqliteIndexStore, connect_memory

from . import fake


@fake
class FakeIndexStore(SqliteIndexStore):
    """An `IndexStore` whose databases are in-memory, keyed by path, one per path."""

    def __init__(self) -> None:
        #: path -> the one long-lived connection held open per path. A shared-cache
        #: in-memory database vanishes when its last connection closes, so the keeper
        #: is what lets `connect` hand out INDEPENDENT connections to one path while a
        #: `close()` of any one of them leaves the rows behind for the next connect.
        self._keepers: dict[str, Connection] = {}

    def connect(self, path: Path | str = ":memory:") -> Connection:
        key = str(path)
        self._keepers.setdefault(key, connect_memory(name=_name(self, key)))
        con = connect_memory(name=_name(self, key))
        self.pragmas(con)
        return con

    def pragmas(self, con: Connection) -> None:
        # WAL and NORMAL are disk durability settings; in memory they are pointless.
        con.execute("pragma journal_mode=MEMORY")
        con.execute("pragma synchronous=OFF")
        con.execute("pragma busy_timeout=30000")


def _name(store: FakeIndexStore, path: str) -> str:
    """The shared-cache name for `path` on `store`, stable for the store's lifetime."""
    return f"{id(store)}:{path}"

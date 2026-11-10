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

    def __init__(self) -> None:
        #: path -> the one long-lived connection kept open per path. A shared-cache
        #: in-memory database dies when its last connection closes, so holding the
        #: keeper open is what lets a reconnect after `close()` still see the rows
        #: written earlier, like reopening one file whose data persists on disk.
        self._keepers: dict[str, Connection] = {}

    def connect(self, path: Path | str = ":memory:") -> Connection:
        key = f"{id(self)}:{path}"
        self._keepers.setdefault(key, connect_memory(name=key))
        con = connect_memory(name=key)
        self.pragmas(con)
        return con

    def pragmas(self, con: Connection) -> None:
        # WAL and NORMAL are disk durability settings; in memory they are pointless.
        con.execute("pragma journal_mode=MEMORY")
        con.execute("pragma synchronous=OFF")
        con.execute("pragma busy_timeout=30000")

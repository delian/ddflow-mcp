"""The `IndexStore` port: connection, pragmas, migration and the index file name.

`sqlite3` is imported here and nowhere else once the call sites move in; the
`sqlite-home` import contract only lets the sites that still import it shrink. The
derived index (`ddflow.infra.store`) is a cache over the event log, never a source of
truth -- it is gitignored, disposable and rebuildable -- so the SQLite seam is cheap to
isolate: one home for the connection, the pragmas, the schema migration and the
fingerprint file name, and an in-memory fake for tests (no tmpdir per case). Only this
home imports sqlite3.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Protocol, runtime_checkable

from .ports import port

#: Re-exported so `FakeIndexStore` opens `:memory:` without importing `sqlite3` itself:
#: the `sqlite-home` contract keeps `sqlite3` in this one module (D-adapters-answers).
Connection = sqlite3.Connection
Row = sqlite3.Row


@port
@runtime_checkable
class IndexStore(Protocol):
    """The sqlite3 seam of the derived index.

    Home: `ddflow.adapters.indexdb`. Single adapter. Callers reach the projection's
    SQLite through this port instead of importing `sqlite3`.
    """

    def filename(self, root: Path | str, fingerprint: str) -> Path:
        """The index file path for `fingerprint` under `root` (`.ddflow/index.db-<fp>`)."""

    def connect(self, path: Path | str) -> Connection:
        """Open `path` for the projection: `Row` rows and the projection pragmas."""

    def pragmas(self, con: Connection) -> None:
        """Set the projection pragmas on an open connection (WAL, NORMAL, 30 s timeout)."""

    def migrate(self, con: Connection, schema: int) -> None:
        """Bring `con` up to `schema`: ensure the version table and record the version."""


class SqliteIndexStore:
    """The default `IndexStore`: a SQLite database on disk under `root`."""

    def filename(self, root: Path | str, fingerprint: str) -> Path:
        return Path(root) / ".ddflow" / f"index.db-{fingerprint}"

    def connect(self, path: Path | str) -> Connection:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(p, timeout=30.0, isolation_level=None)
        con.row_factory = Row
        self.pragmas(con)
        return con

    def pragmas(self, con: Connection) -> None:
        con.execute("pragma journal_mode=WAL")
        con.execute("pragma synchronous=NORMAL")
        con.execute("pragma busy_timeout=30000")

    def migrate(self, con: Connection, schema: int) -> None:
        con.execute("create table if not exists meta(k text primary key, v text)")
        con.execute("insert or replace into meta values('schema', ?)", (str(schema),))


def connect_memory(name: str = ":memory:", timeout: float = 30.0) -> Connection:
    """An in-memory connection with `Row` rows: `FakeIndexStore`'s seam.

    It lives here (not in the fake) so the `sqlite-home` contract keeps `sqlite3` in
    this one module while the fake still opens an in-memory database without its own
    import. `name` keys a *shared-cache* in-memory database, so two connections with the
    same name see the same rows (like reopening one file); the bare `":memory:"` opens a
    private database that a reconnect starts empty.
    """
    if name == ":memory:":
        con = sqlite3.connect(":memory:", timeout=timeout, isolation_level=None)
    else:
        con = sqlite3.connect(
            f"file:db_{name}?mode=memory&cache=shared",
            uri=True,
            timeout=timeout,
            isolation_level=None,
        )
    con.row_factory = Row
    return con


#: The shared default instance.
DEFAULT: IndexStore = SqliteIndexStore()

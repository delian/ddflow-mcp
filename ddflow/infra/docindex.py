"""The documentation index: a derived SQLite file (``.ddflow/local/docs.db``) of files and chunks.

Tables: ``files`` (path, size, mtime_ns, inode, sha256, doctype, indexed_at), ``chunks`` (one
row per `core.docchunk.Chunk`), ``chunks_fts`` (FTS5, porter + unicode61: words) and
``chunks_tri`` (FTS5 trigram: substrings, typos), and ``meta`` (schema version, capabilities).

* **One writer.** `DocIndex.writer` holds a lock file with a timeout, so two processes never
  interleave; readers open the file read-only (WAL) and never wait for it.
* **Atomic.** A missing, corrupt or old-schema index is rebuilt beside the live file and
  swapped in with one rename; a reader keeps whatever it had open.
* **Incremental.** The writer tells the caller what it already knows (`known`); the caller
  skips unchanged files and `put`s the rest, which replaces that file's rows in one
  transaction. A vanished file's rows go with `drop`.
* **No FTS5, no trigram.** A SQLite built without them gets plain tables and ``LIKE``
  search; `SearchResult.mode` and ``note`` say which path answered and why.

Text reaches this module already redacted (``services.docsearch.scope`` redacts a file before
it is chunked): the index never holds what a search must not show. Derived data: delete the file and it is rebuilt.
"""

from __future__ import annotations

import contextlib
import re
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core import clock
from ..core.docchunk import Chunk
from .fsio import LockTimeout, file_lock, lock_path_for

INDEX_LAYOUT = 1  # derived, rebuilt on mismatch: not an upcast contract version
LOCK_TIMEOUT_S = 30.0
MODES = ("words", "substring", "like")
MIN_SUBSTRING = 3  # a trigram index cannot match fewer characters

_BASE = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE files(
  path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
  inode INTEGER NOT NULL, sha256 TEXT NOT NULL, doctype TEXT NOT NULL DEFAULT '',
  indexed_at TEXT NOT NULL);
CREATE TABLE chunks(
  id INTEGER PRIMARY KEY, path TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE,
  chunk_id TEXT NOT NULL UNIQUE, heading TEXT NOT NULL, line_start INTEGER NOT NULL,
  line_end INTEGER NOT NULL, text TEXT NOT NULL, sha256 TEXT NOT NULL,
  kind TEXT NOT NULL, region TEXT NOT NULL, ownership TEXT NOT NULL, doctype TEXT NOT NULL);
CREATE INDEX chunks_path ON chunks(path);
"""
_FTS = """
CREATE VIRTUAL TABLE chunks_fts USING fts5(
  heading, text, content='chunks', content_rowid='id', tokenize='porter unicode61');
CREATE TRIGGER chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, heading, text) VALUES (new.id, new.heading, new.text); END;
CREATE TRIGGER chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, heading, text)
  VALUES ('delete', old.id, old.heading, old.text); END;
"""
_TRI = """
CREATE VIRTUAL TABLE chunks_tri USING fts5(
  text, content='chunks', content_rowid='id', tokenize='trigram');
CREATE TRIGGER chunks_tri_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_tri(rowid, text) VALUES (new.id, new.text); END;
CREATE TRIGGER chunks_tri_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_tri(chunks_tri, rowid, text) VALUES ('delete', old.id, old.text); END;
"""


class DocIndexError(Exception):
    """The index could not be read or written; the message says what to do."""


class IndexMissing(DocIndexError):
    """There is no index (or it is of another schema): build it first."""


def capabilities() -> tuple[bool, bool]:
    """(fts5, trigram) of this SQLite build, probed on a throwaway in-memory database."""
    out = []
    for ddl in (
        "CREATE VIRTUAL TABLE t USING fts5(x)",
        "CREATE VIRTUAL TABLE t USING fts5(x, tokenize='trigram')",
    ):
        try:
            with closing(sqlite3.connect(":memory:")) as con:
                con.execute(ddl)
            out.append(True)
        except sqlite3.Error:
            out.append(False)
    return out[0], out[1]


@dataclass(frozen=True)
class Known:
    """What the index holds about one file: the stat triple and the content hash."""

    size: int
    mtime_ns: int
    inode: int
    sha256: str


@dataclass(frozen=True)
class Hit:
    path: str
    chunk_id: str
    heading: str
    line_start: int
    line_end: int
    text: str
    score: float
    kind: str
    region: str
    ownership: str
    doctype: str


@dataclass(frozen=True)
class SearchResult:
    hits: tuple[Hit, ...] = ()
    mode: str = "words"  # which path answered: words | substring | like
    note: str = ""  # why a requested mode degraded


@dataclass
class Stats:
    files: int = 0
    chunks: int = 0
    fts5: bool = True
    trigram: bool = True
    schema: int = INDEX_LAYOUT
    rebuilt: bool = False


def _heading(c: Chunk) -> str:
    return " > ".join(c.heading_path)


def _now() -> str:
    return clock.now_iso()


class Writer:
    """The one open write transaction: `known`, `put`, `drop`, `touch`. Commits on a clean exit."""

    def __init__(self, con: sqlite3.Connection, rebuilt: bool, fts: bool, tri: bool) -> None:
        self.con, self.rebuilt, self.fts, self.tri = con, rebuilt, fts, tri
        self.written: set[str] = set()  # paths whose rows this writer replaced or removed

    def known(self) -> dict[str, Known]:
        rows = self.con.execute("SELECT path, size, mtime_ns, inode, sha256 FROM files")
        return {r[0]: Known(*r[1:]) for r in rows}

    def put(self, path: str, stat: Known, doctype: str, chunks: Sequence[Chunk]) -> None:
        """Replace ``path``'s file row and chunks. The chunks' text is stored as given: the
        caller redacts BEFORE chunking."""
        self.drop([path])
        self.con.execute(
            "INSERT INTO files VALUES (?,?,?,?,?,?,?)",
            (path, stat.size, stat.mtime_ns, stat.inode, stat.sha256, doctype, _now()),
        )
        self.con.executemany(
            "INSERT INTO chunks(path, chunk_id, heading, line_start, line_end, text, sha256,"
            " kind, region, ownership, doctype) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                (path, c.chunk_id, _heading(c), c.line_start, c.line_end, c.text, c.sha256, c.kind,
                 c.region, c.ownership, doctype or c.doctype)
                for c in chunks
            ],
        )  # fmt: skip
        self.written.add(path)

    def touch(self, path: str, stat: Known) -> None:
        """The file's stat changed but its content did not: remember the new stat, keep chunks."""
        self.con.execute(
            "UPDATE files SET size=?, mtime_ns=?, inode=? WHERE path=?",
            (stat.size, stat.mtime_ns, stat.inode, path),
        )

    def drop(self, paths: Sequence[str]) -> int:
        n = 0
        for p in paths:
            self.con.execute("DELETE FROM chunks WHERE path=?", (p,))
            n += self.con.execute("DELETE FROM files WHERE path=?", (p,)).rowcount
            self.written.add(p)
        return n


@dataclass
class DocIndex:
    """The index at ``path``. ``fts5``/``trigram`` default to what this SQLite build offers; pass
    False to build and search the fallback (tests, a stripped build)."""

    path: Path
    lock_timeout_s: float = LOCK_TIMEOUT_S
    fts5: bool | None = None
    trigram: bool | None = None
    _caps: tuple[bool, bool] = field(init=False, default=(True, True))

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        have = capabilities()
        fts = have[0] if self.fts5 is None else (self.fts5 and have[0])
        self._caps = (
            fts,
            fts and (have[1] if self.trigram is None else (self.trigram and have[1])),
        )

    # -- writing ----------------------------------------------------------------------
    def _state(self) -> str:
        """``ok`` (current schema), ``old`` (another schema), ``bad`` (unreadable) or ``none``."""
        if not self.path.exists():
            return "none"
        try:
            with closing(self._ro()) as con:
                v = con.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
        except sqlite3.Error:
            return "bad"
        return "ok" if v and v[0] == str(INDEX_LAYOUT) else "old"

    def _ro(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=5)

    def _create(self, con: sqlite3.Connection) -> None:
        con.executescript(_BASE)
        if self._caps[0]:
            con.executescript(_FTS)
        if self._caps[1]:
            con.executescript(_TRI)
        con.executemany(
            "INSERT INTO meta VALUES (?,?)",
            [("schema", str(INDEX_LAYOUT)), ("fts5", str(int(self._caps[0]))),
             ("trigram", str(int(self._caps[1]))), ("created_at", _now())],
        )  # fmt: skip

    @contextlib.contextmanager
    def writer(self) -> Iterator[Writer]:
        """Hold the lock and yield a `Writer`; a stale index is rebuilt beside the live one and
        swapped in at the end. Raises `DocIndexError` if the lock is not free in time."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with file_lock(lock_path_for(self.path), self.lock_timeout_s):
                yield from self._write()
        except LockTimeout as exc:
            raise DocIndexError(
                f"the docs index is being written by another process ({exc})"
            ) from None

    def _write(self) -> Iterator[Writer]:
        state = self._state()
        rebuild = state != "ok"
        target = self.path.with_name(self.path.name + ".new") if rebuild else self.path
        if rebuild:
            for suffix in ("", "-wal", "-shm", "-journal"):
                with contextlib.suppress(OSError):
                    Path(f"{target}{suffix}").unlink()
        con = sqlite3.connect(target, timeout=self.lock_timeout_s, isolation_level=None)
        try:
            con.execute("PRAGMA foreign_keys=ON")
            if rebuild:
                con.execute("PRAGMA journal_mode=WAL")
                self._create(con)
                fts, tri = self._caps
            else:
                con.execute("PRAGMA journal_mode=WAL")
                got = dict(con.execute("SELECT key, value FROM meta"))
                fts, tri = got.get("fts5") == "1", got.get("trigram") == "1"
            con.execute("BEGIN IMMEDIATE")
            w = Writer(con, rebuild, fts, tri)
            try:
                yield w
            except BaseException:
                con.execute("ROLLBACK")
                raise
            con.execute("COMMIT")
        finally:
            con.close()
        if rebuild:
            for suffix in ("-wal", "-shm"):  # the live file's stale sidecars are not the new one's
                with contextlib.suppress(OSError):
                    Path(f"{self.path}{suffix}").unlink()
            target.replace(self.path)  # one rename: a reader opens the old file or the new

    # -- reading ----------------------------------------------------------------------
    def stats(self) -> Stats:
        con = self._open()
        with closing(con):
            meta = dict(con.execute("SELECT key, value FROM meta"))
            return Stats(
                con.execute("SELECT count(*) FROM files").fetchone()[0],
                con.execute("SELECT count(*) FROM chunks").fetchone()[0],
                meta.get("fts5") == "1",
                meta.get("trigram") == "1",
                int(meta.get("schema", 0)),
            )

    def _open(self) -> sqlite3.Connection:
        state = self._state()
        if state != "ok":
            what = {"none": "has not been built", "old": "is of another schema"}.get(
                state, "is unreadable"
            )
            raise IndexMissing(f"the docs index {what}: build it first")
        return self._ro()

    def files(self) -> dict[str, Known]:
        with closing(self._open()) as con:
            rows = con.execute("SELECT path, size, mtime_ns, inode, sha256 FROM files")
            return {r[0]: Known(*r[1:]) for r in rows}

    def search(
        self, query: str, *, limit: int = 10, mode: str = "words", doctype: str = ""
    ) -> SearchResult:
        """The best chunks for ``query``. ``words`` is porter-stemmed (FTS5), ``substring`` a
        trigram match (typos, identifiers); either falls back to ``like`` with a note when the
        build or the index lacks it."""
        if mode not in MODES:
            raise ValueError(f"mode is one of {', '.join(MODES)}; got {mode!r}")
        words = re.findall(r"\w+", query)
        if not words and (mode != "substring" or not query.strip()):
            return SearchResult(mode=mode)  # a substring may be all punctuation: `-->`
        with closing(self._open()) as con:
            meta = dict(con.execute("SELECT key, value FROM meta"))
            used, note = mode, ""
            fts_ok = meta.get("fts5") == "1" and self._caps[0]  # built here AND runnable here
            tri_ok = meta.get("trigram") == "1" and self._caps[1]
            if mode == "words" and not fts_ok:
                used, note = (
                    "like",
                    "FTS5 is not available in this SQLite build; searched with LIKE",
                )
            elif mode == "substring" and (not tri_ok or len(query.strip()) < MIN_SUBSTRING):
                used = "like"
                note = (
                    "the trigram index is not available in this SQLite build; searched with LIKE"
                    if not tri_ok
                    else "a substring under 3 characters is searched with LIKE"
                )
            terms = [query.strip()] if mode == "substring" else words  # LIKE: literal, or each word
            try:
                rows = self._query(con, used, query.strip(), terms, limit, doctype)
            except sqlite3.Error as exc:
                raise DocIndexError(f"the docs index could not answer: {exc}") from None
        return SearchResult(tuple(Hit(*r) for r in rows), used, note)

    def _query(
        self,
        con: sqlite3.Connection,
        mode: str,
        raw: str,
        words: list[str],
        limit: int,
        doctype: str,
    ) -> list[Any]:
        cols = (
            "c.path, c.chunk_id, c.heading, c.line_start, c.line_end, c.text, {score}, "
            "c.kind, c.region, c.ownership, c.doctype"
        )
        extra, args = (" AND c.doctype=?", [doctype]) if doctype else ("", [])
        if mode == "words":
            match = " ".join(f'"{w}"' for w in words)
            sql = (
                f"SELECT {cols.format(score='-bm25(chunks_fts, 5.0, 1.0)')} FROM chunks_fts "  # nosec B608 -- only literals are spliced; values are bound
                f"JOIN chunks c ON c.id = chunks_fts.rowid WHERE chunks_fts MATCH ?{extra} "
                "ORDER BY bm25(chunks_fts, 5.0, 1.0), c.chunk_id LIMIT ?"
            )
            return con.execute(sql, [match, *args, limit]).fetchall()
        if mode == "substring":
            match = '"' + raw.replace('"', '""') + '"'
            sql = (
                f"SELECT {cols.format(score='-bm25(chunks_tri)')} FROM chunks_tri "  # nosec B608 -- only literals are spliced; values are bound
                f"JOIN chunks c ON c.id = chunks_tri.rowid WHERE chunks_tri MATCH ?{extra} "
                "ORDER BY bm25(chunks_tri), c.chunk_id LIMIT ?"
            )
            return con.execute(sql, [match, *args, limit]).fetchall()
        like = " AND ".join("lower(c.text) LIKE ? ESCAPE '\\'" for _ in words)
        pats = ["%" + w.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                for w in words]  # fmt: skip
        sql = (
            f"SELECT {cols.format(score='0.0')} FROM chunks c WHERE {like}{extra} "  # nosec B608 -- only literals are spliced; values are bound
            "ORDER BY c.path, c.line_start LIMIT ?"
        )
        return con.execute(sql, [*pats, *args, limit]).fetchall()


__all__ = [
    "DocIndex",
    "DocIndexError",
    "Hit",
    "IndexMissing",
    "Known",
    "SearchResult",
    "Stats",
    "Writer",
]

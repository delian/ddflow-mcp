"""The schema half of `Store`: the connection, the tables, and staleness."""

from __future__ import annotations

import contextlib
import os
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

from ...config import Config
from ...core import textsim
from ..log import EventLog
from ._fingerprint import (
    _SEARCH_COLS,
    SCHEMA,
    _capability_tables,
)

#: SQLite VM steps between two checks of a `read_only_rows` time budget.
PROGRESS_STEPS = 1000


class ReadOnlyError(Exception):
    """A database another tool owns could not be read, with the reason."""


class ReadOnlyTimeout(ReadOnlyError):
    """A read of such a database ran over its time budget."""


def read_only_rows(path: Path, sql: str, params: tuple = (), *, timeout_s: float) -> list[tuple]:
    """The rows of one query on a SQLite file ddflow does not own: opened read-only, so it
    can neither change the file nor create one, and stopped at ``timeout_s`` because such a
    file can be many gigabytes. Raises `ReadOnlyError` with the reason, a timeout as
    `ReadOnlyTimeout`."""
    deadline = time.monotonic() + timeout_s
    try:
        con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=timeout_s)
    except (sqlite3.Error, OSError, RuntimeError) as exc:  # RuntimeError: a symlink loop
        raise ReadOnlyError(str(exc)) from exc
    try:
        con.set_progress_handler(lambda: time.monotonic() > deadline, PROGRESS_STEPS)
        return con.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        if "interrupted" in str(exc):
            raise ReadOnlyTimeout(f"over its {timeout_s}s budget") from exc
        raise ReadOnlyError(str(exc)) from exc
    except sqlite3.Error as exc:
        raise ReadOnlyError(str(exc)) from exc
    finally:
        con.close()


class _SchemaMixin:
    def __init__(self, root: Path, cfg: Config | None = None) -> None:
        self.root = Path(root)
        self.cfg = cfg or Config.load(root)
        # The capability probes and the fingerprint are reached through the package
        # namespace at call time (not imported here), so tests that monkeypatch
        # ``store._has_trigram`` or ``store.code_fingerprint`` still take effect.
        pkg = sys.modules[__package__]
        # One index per code fingerprint: `index.db-<fingerprint>` (covered by the
        # `index.db-*` line every adopted project's `.ddflow/.gitignore` already has).
        self.path = self.root / ".ddflow" / f"index.db-{pkg.code_fingerprint()}"
        self.fts = pkg._has_fts5() and self.cfg.lessons.search_backend == "fts5"
        #: The trigram index beside each FTS5 table; without it the same ranking is
        #: computed in Python (`rank.substring_bm25`), over the same candidates.
        self.trigram = self.fts and pkg._has_trigram()

    def _ensure_dir(self) -> None:
        """Create `.ddflow/` only when something is actually about to be written.

        It used to happen in `__init__`, and `Ctx` builds a Store for EVERY command —
        so `ddflow status` in a repository that had never run `ddflow init` created
        `.ddflow/` and exited 0, as though the project had adopted the tool. A
        read-only question must not leave a mark. The same rule was already applied to
        the MCP server's handshake for the same reason; this is the second door onto
        the same mistake.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        self._ensure_dir()
        con = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("pragma journal_mode=WAL")
        con.execute("pragma synchronous=NORMAL")
        con.execute("pragma busy_timeout=30000")
        return con

    # -- schema ---------------------------------------------------------------------
    def init(self, con: sqlite3.Connection) -> None:
        con.executescript("""
        create table if not exists meta(k text primary key, v text);
        create table if not exists items(
            id text primary key, kind text, title text, parent text, state text,
            needs text, globs text, tags text, priority integer, body text,
            worktree text, branch text, merged_sha text, blocked_reason text,
            lease_holder text, lease_expires real, gates text,
            created_at text, completed_at text);
        create index if not exists items_parent on items(parent);
        create index if not exists items_state  on items(state);
        create table if not exists lessons(
            id text primary key, title text, rule text, why text, how text,
            seen_in text, tags text, at text, superseded_by text);
        create table if not exists research(
            id text primary key, question text, claim text, verdict text,
            probe text, sources text, at text, item text);
        create table if not exists decisions(
            id text primary key, title text, context text, decision text,
            consequences text, alternatives text, globs text, tags text,
            status text, decided_by text, superseded_by text, at text, item text);
        create table if not exists bugs(
            id text primary key, item text, summary text, found_at text,
            fixed_at text, regression_test text, lesson text,
            invalid_at text, invalid_reason text, evidence text);
        create table if not exists prompts(
            session text, seq integer, at text, item text, text text,
            role text default 'prompt',
            primary key(session, seq));
        create table if not exists cadences(name text, at text, by text, result text);
        create table if not exists memories(
            id text primary key, text text, tags text, at text, origin_at text,
            by text, source text);
        create table if not exists records(
            id text primary key, kind text, rid text, title text, body text,
            status text, at text);
        create table if not exists similar_docs(
            ord integer primary key, id text, kind text, item text, digest text);
        create table if not exists similar_terms(
            term text primary key, docs blob, weights blob) without rowid;
        """)
        if self.fts:
            con.executescript("""
            create virtual table if not exists lessons_fts using fts5(
                id unindexed, title, rule, why, how, tags,
                tokenize='porter unicode61');
            create virtual table if not exists research_fts using fts5(
                id unindexed, question, claim, probe, verdict,
                tokenize='porter unicode61');
            create virtual table if not exists decisions_fts using fts5(
                id unindexed, title, context, decision, consequences, alternatives,
                tokenize='porter unicode61');
            create virtual table if not exists prompts_fts using fts5(
                id unindexed, text, tokenize='porter unicode61');
            create virtual table if not exists bugs_fts using fts5(
                id unindexed, summary, lesson, tokenize='porter unicode61');
            create virtual table if not exists items_fts using fts5(
                id unindexed, title, body, tags, tokenize='porter unicode61');
            create virtual table if not exists memories_fts using fts5(
                id unindexed, text, tags, tokenize='porter unicode61');
            create virtual table if not exists records_fts using fts5(
                id unindexed, title, body, tokenize='porter unicode61');
            """)
        if self.trigram:
            for table in _SEARCH_COLS:
                con.execute(
                    # bandit B608: `table` is a key of the fixed `_SEARCH_COLS`.
                    f"create virtual table if not exists {table}_tri using fts5("  # nosec B608
                    "id unindexed, text, tokenize='trigram')"
                )
        con.execute("insert or replace into meta values('schema', ?)", (str(SCHEMA),))

    def stale(self, log: EventLog) -> bool:
        """Is the index behind the log? Compared by (schema, shard count, byte total,
        last lamport) — four cheap numbers, any of which changing means re-derive.

        Bytes and shards, not an event count: two agents can append concurrently, so
        the highest Lamport can stay put while a second agent's shard grows.
        """
        if not self.path.exists():
            return True
        try:
            with closing(self.connect()) as con:
                row = {r["k"]: r["v"] for r in con.execute("select k,v from meta")}
                cap_ok = _capability_tables(con, self.fts, self.trigram)
        except sqlite3.DatabaseError:
            return True
        if row.get("schema") != str(SCHEMA):
            return True
        if row.get("similar_version") != str(textsim.VERSION):
            return True
        # An index built where SQLite had no trigram tokenizer has no `_tri` tables filled;
        # read where it has one, it would rank on empty tables. A change of capability is
        # a rebuild -- and so is a meta that CLAIMS the capability while its tables are
        # absent (the reconcile `_rebuild_locked` refuses stamped `trigram=1` over an index
        # with no `_tri` tables at all).
        if (
            row.get("fts", "0") != ("1" if self.fts else "0")
            or row.get("trigram", "0") != ("1" if self.trigram else "0")
            or not cap_ok
        ):
            return True
        # `EventLog.mark()` is O(shards); the old comparison called `read_all()` to
        # decide whether `read_all()` was needed, which is the shape of the problem
        # rather than a solution to it.
        mark = log.mark()
        behind = (
            row.get("lamport") != str(mark.lamport)
            or row.get("bytes") != str(mark.bytes)
            or row.get("shards") != str(mark.count)
        )
        if not behind:
            self._touch_if_old()
        return behind

    def _touch_if_old(self) -> None:
        """Mark the index as used: reads never write the file, so without this its mtime says
        when it was BUILT, and `_collect_stale_indexes` of another checkout would take a
        long-lived reader's index for an abandoned one."""
        with contextlib.suppress(OSError):
            if time.time() - self.path.stat().st_mtime > self.cfg.store.index_touch_s:
                os.utime(self.path)

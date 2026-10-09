"""The SQLite projection — a cache and a search index, never a source of truth.

This database is **gitignored, disposable and rebuildable**. Delete it and
``ddflow rebuild`` re-derives every row from the event log. That is not a slogan; it
is enforced by ``tests/test_store.py::test_dropping_the_db_changes_nothing``, which
compares every query before and after ``rm``.

Keeping the index non-authoritative buys three things a database-of-record cannot:

* two agents on two git branches never conflict, because they merge *log files*, not
  database pages;
* a corrupted index is a 200 ms annoyance instead of a lost project;
* the index schema can change without a migration — bump ``SCHEMA`` and rebuild.

What it is genuinely *for* is retrieval. Folding the log answers "what is the state";
it does not answer "which four of my 400 lessons bear on the task I am about to
start". That is a ranking problem, and FTS5's BM25 is a better answer than anything
worth hand-rolling. Where SQLite was built without FTS5 the store degrades to LIKE
scanning, which is slower and worse-ranked but never absent — a search that vanishes
on some machines is worse than one that is merely weaker.
"""

from __future__ import annotations

import contextlib
import errno
import functools
import json
import os
import re
import secrets
import sqlite3
import sys
import time
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ddflow import FORMAT_LEVEL, __version__

from ..config import Config
from ..core import digest as D
from ..core import porter, textsim
from ..core.model import State, fold
from ..core.rank import TRIGRAM_MIN, bm25, fuse, rerank, substring_bm25
from ..core.textcut import clip
from ..infra.log import EventLog, _flock

SCHEMA = 10

#: How long an index built by OTHER code (a different fingerprint, or the pre-fingerprint
#: `index.db`) is kept after its last write before a rebuild garbage-collects it. Worktrees
#: on different ddflow versions each keep their own index meanwhile (D-compat).
STALE_INDEX_DAYS = 7.0
#: An index in use is touched at most this often, so its mtime means "last used", not "last
#: rebuilt", and `STALE_INDEX_DAYS` of silence really is disuse.
INDEX_TOUCH_S = 86400.0
#: Fewer source files than this found under `ddflow/core/` means there is no source tree.
MIN_FINGERPRINT_SOURCES = 3

#: Every file a fingerprinted index (or an earlier one) can leave beside it.
_INDEX_FILE = re.compile(r"^index\.db(-[0-9a-f]{16})?(-wal|-shm|-lock|-rebuilding\..*)?$")


@functools.lru_cache(maxsize=1)
def code_fingerprint() -> str:
    """What built an index, in 16 hex digits: the source of the fold and of the projection
    (everything under `ddflow/core/` and this module), SCHEMA, the similarity VERSION and
    the on-disk FORMAT_LEVEL. A change to any of them is a different index, so nobody has
    to remember to bump a constant, and two checkouts running different code each keep their
    own index instead of rebuilding each other's (D-compat)."""
    h = D.hasher(f"{SCHEMA}/{textsim.VERSION}/{FORMAT_LEVEL}".encode())
    here = Path(__file__).resolve()
    core = here.parent.parent / "core"
    sources = sorted([*core.rglob("*.py"), here])
    for f in sources:
        h.update(f.relative_to(here.parent.parent).as_posix().encode())
        with contextlib.suppress(OSError):
            h.update(f.read_bytes())
    if len(sources) < MIN_FINGERPRINT_SOURCES:
        # No source tree to read (a zipped or bytecode-only install): the release stands in
        # for the code rather than the fingerprint silently becoming a constant.
        h.update(__version__.encode())
    return h.hexdigest()[:16]


#: Shortest token kept from a user query. One-character tokens match almost everything
#: and rank nothing, so they cost index time and return noise.
MIN_TERM_CHARS = textsim.MIN_WORD_CHARS

#: How long a rebuild waits for another one to finish. A rebuild runs at ~12k events/s,
#: so this is minutes of headroom, not an expected wait.
REBUILD_LOCK_TIMEOUT_S = 120.0
#: A rebuild's temp index left behind is removed only once untouched this long: a live
#: rebuild (an older lock-less ddflow's, or one the lock did not serialise) may still be
#: building into it.
REBUILD_TEMP_MAX_AGE_S = 3600.0
#: The OSError numbers and SQLite result codes that mean "the machine said no", not "the
#: projection is wrong" -- the only failures `Store.ensure` answers from the log. EPERM is
#: left out on purpose: the index is written only where `.ddflow/` already is, and there
#: a permission problem reads as EACCES (or as SQLite's CANTOPEN, see `_cannot_write`);
#: an EPERM is far more often a code path doing something it may not.
_ENV_ERRNOS = frozenset({errno.ENOSPC, errno.EACCES, errno.EROFS, errno.EDQUOT, errno.EIO})
_ENV_SQLITE = frozenset(
    {
        sqlite3.SQLITE_BUSY,
        sqlite3.SQLITE_LOCKED,
        sqlite3.SQLITE_IOERR,
        sqlite3.SQLITE_READONLY,
        sqlite3.SQLITE_FULL,
    }
)


def _environmental(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, sqlite3.OperationalError):
        # By SQLite's result code, never by the message: a message carries identifiers,
        # and `no such column: full_text` must not read as a full disk. The low byte is
        # the primary code an extended one (SQLITE_IOERR_WRITE, ...) belongs to.
        code = getattr(exc, "sqlite_errorcode", None)
        return code is not None and (code & 0xFF) in _ENV_SQLITE
    return isinstance(exc, OSError) and exc.errno in _ENV_ERRNOS


def _has_fts5() -> bool:
    with closing(sqlite3.connect(":memory:")) as c:
        return any("FTS5" in r[0] for r in c.execute("pragma compile_options"))


def _has_trigram() -> bool:
    """Does this SQLite's FTS5 have the ``trigram`` tokenizer (3.34+)? Probed, not assumed."""
    with closing(sqlite3.connect(":memory:")) as c:
        try:
            c.execute("create virtual table t using fts5(a, tokenize='trigram')")
        except sqlite3.OperationalError:
            return False
        return True


#: The columns each searchable table is ranked on, by both rankers and the trigram index.
_SEARCH_COLS: dict[str, tuple[str, ...]] = {
    "lessons": ("title", "rule", "why", "how", "tags"),
    "decisions": ("title", "context", "decision", "consequences", "alternatives"),
    "research": ("question", "claim", "probe", "verdict"),
    "bugs": ("summary", "lesson"),
    "prompts": ("text",),
    "items": ("title", "body", "tags"),
    "memories": ("text", "tags"),
}


class Store:
    def __init__(self, root: Path, cfg: Config | None = None) -> None:
        self.root = Path(root)
        self.cfg = cfg or Config.load(root)
        # One index per code fingerprint: `index.db-<fingerprint>` (covered by the
        # `index.db-*` line every adopted project's `.ddflow/.gitignore` already has).
        self.path = self.root / ".ddflow" / f"index.db-{code_fingerprint()}"
        self.fts = _has_fts5() and self.cfg.lessons.search_backend == "fts5"
        #: The trigram index beside each FTS5 table; without it the same ranking is
        #: computed in Python (`rank.substring_bm25`), over the same candidates.
        self.trigram = self.fts and _has_trigram()

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
        except sqlite3.DatabaseError:
            return True
        if row.get("schema") != str(SCHEMA):
            return True
        if row.get("similar_version") != str(textsim.VERSION):
            return True
        # An index built where SQLite had no trigram tokenizer has no `_tri` tables filled;
        # read where it has one, it would rank on empty tables. A change of capability is
        # a rebuild.
        if row.get("trigram", "0") != ("1" if self.trigram else "0"):
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
            if time.time() - self.path.stat().st_mtime > INDEX_TOUCH_S:
                os.utime(self.path)

    # -- projection -----------------------------------------------------------------
    @property
    def lock_path(self) -> Path:
        # `index.db-*`: the name every adopted project's `.ddflow/.gitignore` already ignores.
        return self.path.with_name(self.path.name + "-lock")

    def rebuild(self, log: EventLog) -> State:
        """Drop everything and re-derive from the log, one rebuild at a time.

        Serialised by a lock beside the index (Bcdfb199cc0): two rebuilds used to share
        one temp path with no lock, so one unlinked or published the other's half-built
        file -- `FileNotFoundError`, `no such table: meta`, or a SIGBUS from SQLite's
        mapped `-shm` being truncated under it.
        """
        return self._rebuild(log, only_if_stale=False)

    def _rebuild(self, log: EventLog, *, only_if_stale: bool) -> State:
        """Take the rebuild lock and rebuild; with `only_if_stale`, first ask again under
        the lock, so a rebuild another process finished while this one waited is not
        redone. The ONE place the lock is taken."""
        # `rebuild` writes its temp database beside the index rather than through
        # `connect`, so it needs the directory itself. Only a rebuild asks; no read
        # path does, which is the whole point — `ddflow status` must not adopt a
        # repository that never ran `ddflow init`.
        self._ensure_dir()
        with _flock(self.lock_path, REBUILD_LOCK_TIMEOUT_S):
            if only_if_stale and not self.stale(log):
                return fold(log.read_all(), strict=False)
            return self._rebuild_locked(log)

    def _rebuild_locked(self, log: EventLog) -> State:
        """Drop everything and re-derive from the log. The ONLY write path. The caller
        holds `lock_path`.

        There is deliberately no incremental update. An incremental projector is a
        second implementation of `fold` that can disagree with it, and a cache that
        disagrees with its source silently is worse than no cache. Rebuild measured
        at ~12k events/second, which for any realistic backlog is far below the cost
        of the git operations surrounding it.
        """
        # The fingerprint FIRST, then the events it describes. Taken afterwards it
        # describes a log NEWER than the projection: an append landing between the two
        # is in the fingerprint and not in the index, so the next `stale()` matches,
        # returns False, and those events are never projected -- `recall` silently
        # omits them, permanently, if the log then stops growing. Taken first the error
        # is in the safe direction: the fingerprint is at or behind what was projected,
        # so at worst one unnecessary rebuild, never a missed event. (Raised
        # THEORETICAL by the cross-family critic 2026-09-24; probe in
        # `tests/test_critic_findings.py`.)
        fingerprint = log.mark()
        events = log.read_all()
        state = fold(events, strict=False)
        # Temp files left by a rebuild that died -- ours, `index.db-rebuilding.*`, and the
        # fixed `index.rebuilding*` an older, lock-less ddflow used -- go only once they
        # are older than any rebuild should take. Age, not the lock, decides: the lock is
        # advisory (an older ddflow never takes it, and a filesystem may not honour it),
        # and a live rebuild's temp must never be unlinked under it. Each file is aged by
        # its own mtime; a rebuild runs at ~12k events/s, so the hour is far beyond one.
        for pattern in (
            f"{self.path.name}-rebuilding*",
            f"{self.path.stem}.rebuilding*",
            "index.db-rebuilding*",  # an index named before fingerprints, and its fixed-name
            "index.rebuilding*",  # predecessor
        ):
            for old in self.path.parent.glob(pattern):
                with contextlib.suppress(OSError):
                    if time.time() - old.stat().st_mtime > REBUILD_TEMP_MAX_AGE_S:
                        old.unlink()
        # A name of its own all the same: the lock is advisory, and a temp file that
        # nothing else can name cannot be unlinked or published by anything else.
        tmp = self.path.with_name(
            f"{self.path.name}-rebuilding.{os.getpid()}.{secrets.token_hex(4)}"
        )
        try:
            return self._build_into(tmp, state, events, fingerprint)
        finally:
            for suf in ("", "-wal", "-shm"):
                tmp.with_name(tmp.name + suf).unlink(missing_ok=True)

    def _build_into(self, tmp: Path, state: State, events: list, fingerprint) -> State:
        """Write the projection into `tmp` and publish it over the index."""
        con = sqlite3.connect(tmp, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("pragma journal_mode=WAL")
        try:
            self.init(con)
            con.execute("BEGIN IMMEDIATE")
            for it in state.items.values():
                if it.removed:
                    continue
                con.execute(
                    "insert or replace into items values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        it.id,
                        it.kind,
                        it.title,
                        it.parent,
                        it.state,
                        json.dumps(it.needs),
                        json.dumps(it.globs),
                        json.dumps(it.tags),
                        it.priority,
                        it.body,
                        it.worktree,
                        it.branch,
                        it.merged_sha,
                        it.blocked_reason,
                        it.lease.holder if it.lease else "",
                        (it.lease.renewed_at + it.lease.ttl_s) if it.lease else 0.0,
                        json.dumps({g: asdict(r) for g, r in it.gates.items()}),
                        it.created_at,
                        it.completed_at,
                    ),
                )
                if self.fts:
                    con.execute(
                        "insert into items_fts values(?,?,?,?)",
                        (it.id, it.title, it.body, " ".join(it.tags)),
                    )
            for ls in state.lessons.values():
                con.execute(
                    "insert or replace into lessons values(?,?,?,?,?,?,?,?,?)",
                    (
                        ls.id,
                        ls.title,
                        ls.rule,
                        ls.why,
                        ls.how,
                        json.dumps(ls.seen_in),
                        json.dumps(ls.tags),
                        ls.at,
                        ls.superseded_by,
                    ),
                )
                if self.fts:
                    con.execute(
                        "insert into lessons_fts values(?,?,?,?,?,?)",
                        (ls.id, ls.title, ls.rule, ls.why, ls.how, " ".join(ls.tags)),
                    )
            for rn in state.research.values():
                con.execute(
                    "insert or replace into research values(?,?,?,?,?,?,?,?)",
                    (
                        rn.id,
                        rn.question,
                        rn.claim,
                        rn.verdict,
                        rn.probe,
                        json.dumps(rn.sources),
                        rn.at,
                        rn.item,
                    ),
                )
                if self.fts:
                    con.execute(
                        "insert into research_fts values(?,?,?,?,?)",
                        (rn.id, rn.question, rn.claim, rn.probe, rn.verdict),
                    )
            _insert_decisions(con, state, self.fts)
            _insert_memories(con, state, self.fts)
            _insert_bugs(con, state, self.fts)
            _insert_sessions(con, state, self.fts)
            if self.trigram:
                _fill_trigram(con)
            _insert_similar(con, similar_records(state))
            for name, runs in state.cadences.items():
                for r in runs:
                    con.execute(
                        "insert into cadences values(?,?,?,?)",
                        (name, r["at"], r["by"], r["result"]),
                    )
            con.execute("insert or replace into meta values('events', ?)", (str(len(events)),))
            # Written from the SAME cheap read `stale()` will use -- not recomputed
            # from `events`, which cannot produce a byte count at all -- and captured
            # BEFORE the events were read, so it can only under-report the log.
            shards, high, size = fingerprint.count, fingerprint.lamport, fingerprint.bytes
            for k, v in (("shards", shards), ("bytes", size)):
                con.execute("insert or replace into meta values(?, ?)", (k, str(v)))
            con.execute(
                "insert or replace into meta values('lamport', ?)",
                (str(high),),
            )
            con.execute(
                "insert or replace into meta values('built_at', ?), ('fts', ?), ('trigram', ?)",
                (str(time.time()), "1" if self.fts else "0", "1" if self.trigram else "0"),
            )
            con.execute("COMMIT")
        finally:
            con.close()
        # Publish atomically: readers see either the old index or the new one, never
        # a half-built one. WAL sidecars are removed first so the replaced file is
        # self-contained.
        for suf in ("-wal", "-shm"):
            tmp.with_name(tmp.name + suf).unlink(missing_ok=True)
            self.path.with_name(self.path.name + suf).unlink(missing_ok=True)
        tmp.replace(self.path)
        self._collect_stale_indexes()
        return state

    def _collect_stale_indexes(self) -> list[Path]:
        """Delete the indexes of OTHER code untouched for `STALE_INDEX_DAYS`. Best effort,
        and never this fingerprint's own files: an index in use is touched by `stale()` at least
        daily, and a mistaken removal only costs that checkout a rebuild."""
        gone: list[Path] = []
        mine = self.path.name
        cutoff = time.time() - STALE_INDEX_DAYS * 86400
        for f in self.path.parent.glob("index.db*"):
            if f.name.startswith(mine) or not _INDEX_FILE.match(f.name):
                continue
            with contextlib.suppress(OSError):
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    gone.append(f)
        return gone

    def ensure(self, log: EventLog) -> State:
        """The folded state, with the index made current on the way when it is behind.

        A READ path, so it never fails for the index's sake: the index is a cache, and
        when it cannot be rebuilt (the lock held past its timeout, a full disk, an
        unwritable directory) the state still comes from the log, with a warning. A
        rebuild that another process finished while this one waited for the lock is not
        redone.
        """
        if not self.stale(log):
            return fold(log.read_all(), strict=False)
        try:
            return self._rebuild(log, only_if_stale=True)
        except (TimeoutError, OSError, sqlite3.OperationalError) as exc:
            # Only the environment (a held lock, a full or read-only disk, a locked
            # database) degrades to the log. A bug in the projection -- a bad statement,
            # a missing column, an IntegrityError, a fold error -- still propagates, so a
            # broken rebuild cannot hide behind this.
            if not (_environmental(exc) or self._cannot_write(exc)):
                raise
            print(
                f"ddflow: the index {self.path} could not be rebuilt ({exc}); "
                f"answering from the log. `ddflow rebuild` retries.",
                file=sys.stderr,
            )
            return fold(log.read_all(), strict=False)

    def _cannot_write(self, exc: BaseException) -> bool:
        """SQLITE_CANTOPEN counts as the environment only when the index's directory
        EXISTS and is not writable. From a wrong or missing path it is a bug and must
        surface; only the plain code is taken (an extended one, such as CANTOPEN_ISDIR,
        names a path problem)."""
        parent = self.path.parent
        return (
            getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_CANTOPEN
            and parent.is_dir()
            and not os.access(parent, os.W_OK)
        )

    # -- search ---------------------------------------------------------------------
    def search(
        self, table: str, query: str, limit: int = 5, *, rerank_by_likeness: bool = False
    ) -> list[dict[str, Any]]:
        """Rank ``table`` against ``query``: BM25 over the porter words fused (reciprocal
        rank) with a trigram BM25 over the substrings, best first.

        Both lists exist on every machine. Where SQLite has FTS5 they come from its indexes;
        where it has none (or no trigram tokenizer) the same lists are computed in Python
        over the same columns and the same query terms. Both lists are identical in their
        candidates everywhere: the substring list by construction, the WORD list because the
        fallback splits words as FTS5's unicode61 tokenizer does (`textsim.fts_words`) and
        stems them as its porter tokenizer does (`core.porter`), each checked against FTS5.
        ``rerank_by_likeness`` additionally reorders the fused top by fuzzy likeness to the
        query (`rank.rerank`: rapidfuzz when installed, else difflib).

        The user query is passed through ``_fts_query``, which strips FTS operator
        characters and ORs the terms. Raw user text reaching an FTS5 MATCH is a syntax
        error waiting to happen — an unbalanced quote or a bare ``-`` raises, and a
        search that throws on a normal question is a search nobody uses.
        """
        cols = _SEARCH_COLS[table]
        pool = max(limit * _POOL, _POOL_MIN)
        terms = _terms(query)
        if not terms:  # nothing to rank on, on every machine
            return []
        with closing(self.connect()) as con:
            self.init(con)
            scan: tuple[list[str], list[dict[str, Any]], list[str]] | None = None

            def scanned() -> tuple[list[str], list[dict[str, Any]], list[str]]:
                nonlocal scan
                if scan is None:
                    rows = [dict(r) for r in con.execute(f"select * from {table}")]  # nosec B608
                    keys = [_row_key(table, r, n) for n, r in enumerate(rows)]
                    if table == "prompts":  # the id the FTS path gives a prompt, too
                        rows = [{**r, "id": k} for r, k in zip(rows, keys, strict=True)]
                    scan = (keys, rows, [" ".join(str(r.get(c) or "") for c in cols) for r in rows])
                return scan

            def python_rankings(which: str) -> list[str]:
                keys, _, texts = scanned()
                if which == "bm25":
                    score = bm25(
                        [_fallback_words(t) for t in texts],
                        _fallback_words(" ".join(terms)),
                    )
                else:
                    score = substring_bm25(texts, terms)
                return [keys[i] for i in sorted(score, key=lambda i: (-score[i], keys[i]))][:pool]

            lists: list[list[str]] = []
            if self.fts:
                lists = [self._fts_ranking(con, f"{table}_fts", _fts_query(query), pool)]
                lists.append(
                    self._fts_ranking(con, f"{table}_tri", _tri_query(terms), pool)
                    if self.trigram
                    else python_rankings("trigram")
                )
            from_fts = any(lists)
            if not from_fts:
                # No FTS5, or it found nothing: both rankers over every row, in Python.
                lists = [python_rankings("bm25"), python_rankings("trigram")]
            top = fuse(lists, pool if rerank_by_likeness else limit)
            if from_fts:
                hits = self._fetch(con, table, top)
            else:
                keys, rows, _ = scanned()
                by_key = dict(zip(keys, rows, strict=True))
                hits = {i: by_key[i] for i in top}
            ordered = [hits[i] for i in top if i in hits]
            if rerank_by_likeness and ordered:
                cands = [
                    (i, " ".join(str(hits[i].get(c) or "") for c in cols)) for i in top if i in hits
                ]
                ordered = [hits[i] for i in rerank(query, cands, limit)]
            return ordered[:limit]

    @staticmethod
    def _fts_ranking(con, index: str, match: str, limit: int) -> list[str]:
        """Ids of the FTS5 table ``index`` matching ``match``, best BM25 first; empty for no
        expression, no such table or one FTS5 refuses."""
        if not match:
            return []
        try:
            rows = con.execute(
                # bandit B608: `index` is `<key of _SEARCH_COLS>_fts` or `_tri`, built here.
                f"select id from {index} where {index} match ? "  # nosec B608
                f"order by bm25({index}), id limit ?",
                (match, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [r["id"] for r in rows]

    @staticmethod
    def _fetch(con, table: str, ids: list[str]) -> dict[str, dict[str, Any]]:
        """The rows for FTS ids. A prompt's id is ``<session>#<role letter><seq>``, which
        names its row by (session, seq) -- the letter says whether the operator spoke or the
        agent noted."""
        out: dict[str, dict[str, Any]] = {}
        if table == "prompts":
            for ident in ids:
                sess, _, tail = ident.partition("#")
                role, digits = (tail[:1], tail[1:]) if tail[:1].isalpha() else ("p", tail)
                seq = int(digits or 0) + _LETTER_OFFSET.get(role, 0)
                r = con.execute("select * from prompts where session=? and seq=?", (sess, seq))
                row = r.fetchone()
                if row:
                    out[ident] = {**dict(row), "id": ident}
            return out
        if not ids:
            return out
        ph = ",".join("?" * len(ids))
        # bandit B608: only `?` placeholders are interpolated, and `table` is a key of
        # `_SEARCH_COLS`.
        for r in con.execute(f"select * from {table} where id in ({ph})", ids):  # nosec B608
            out[r["id"]] = dict(r)
        return out

    def query(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        with closing(self.connect()) as con:
            self.init(con)
            return [dict(r) for r in con.execute(sql, args)]


def _fts_query(text: str) -> str:
    """Turn arbitrary user text into a safe FTS5 expression.

    Terms are quoted individually and ORed. Quoting is what makes an operator
    character inert; ORing is what makes a multi-word question behave like a
    relevance query instead of a conjunction that matches nothing.
    """
    return " OR ".join(f'"{t}"' for t in _terms(text))


def _tri_query(terms: list[str]) -> str:
    """The FTS5 expression for the trigram index: each term as a quoted substring, ORed.
    Terms under three characters match nothing there, so they are left out."""
    return " OR ".join(f'"{t}"' for t in terms if len(t) >= TRIGRAM_MIN)


def _row_key(table: str, row: dict[str, Any], n: int) -> str:
    """A row's id in the fallback ranking: its ``id``, or a prompt's ``<session>#<letter><seq>``
    -- the FTS id -- so both paths fuse and break ties on the same key."""
    if table != "prompts":
        return str(row.get("id", n))
    role = str(row.get("role") or "prompt")
    return f"{row.get('session', '')}#{role[0]}{int(row.get('seq', 0)) - _ROLE_OFFSET.get(role, 0)}"


def _fill_trigram(con) -> None:
    """Copy each table's ranked text from its porter FTS5 table into the trigram one."""
    for table, cols in _SEARCH_COLS.items():
        text = " || ' ' || ".join(f"coalesce({c}, '')" for c in cols)
        con.execute(
            # bandit B608: names come from the fixed `_SEARCH_COLS`.
            f"insert into {table}_tri(id, text) select id, {text} from {table}_fts"  # nosec B608
        )


#: How many terms of a query reach any ranker.
_FTS_TERMS = 12
#: Each ranker is asked for this many times the wanted hits (at least `_POOL_MIN`) before fusion.
_POOL, _POOL_MIN = 4, 20


def _fallback_words(text: str) -> list[str]:
    """The words the FTS5-less fallback ranks on: every word, lower-cased and stemmed the way
    FTS5's porter tokenizer stems it, so a query returns the same rows without FTS5."""
    return [porter.stem(w) for w in textsim.fts_words(text)]


def _terms(query: str) -> list[str]:
    """The terms every ranker is asked for, on every machine: the query's words of at least
    ``MIN_TERM_CHARS``, the first ``_FTS_TERMS`` of them."""
    return textsim.words(query, min_len=MIN_TERM_CHARS)[:_FTS_TERMS]


#: What `recall` searches, in the order a reader should weigh them. Decisions first
#: because they are binding, lessons next because they are transferable, then evidence,
#: then history. The order is the answer to "which of these should change what I do".
RECALL_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("decisions", "DECISION", "binding — follow it unless the operator says otherwise"),
    ("lessons", "LESSON", "learned the hard way here"),
    ("memories", "MEMORY", "an operational fact about this machine or repository"),
    ("research", "RESEARCH", "already investigated; check the verdict before redoing it"),
    ("bugs", "BUG", "this has broken before"),
    ("items", "TASK", "similar work already planned or done"),
    ("prompts", "PROMPT/NOTE", "the operator asked, or an agent recorded, something like this"),
)


def _fit(text: str, width: int) -> str:
    """The first ``width`` characters of a hit's body: no marker, no trimming."""
    return clip(text, width, rstrip=False)


def summarise_row(table: str, row: dict[str, Any], width: int = 240) -> tuple[str, str]:
    """(headline, body) for one hit, per source table."""
    if table == "decisions":
        head = f"{row.get('title', '')}"
        if row.get("status") != "accepted" or row.get("superseded_by"):
            head += f"  [{row.get('status')}"
            head += f" -> {row['superseded_by']}]" if row.get("superseded_by") else "]"
        return head, _fit(row.get("decision") or "", width)
    if table == "lessons":
        return row.get("title", ""), _fit(row.get("rule") or "", width)
    if table == "research":
        return (
            f"{row.get('question', '')}  [{row.get('verdict', '')}]",
            _fit(row.get("claim") or "", width),
        )
    if table == "bugs":
        # Three states, never two: a false finding labelled "fixed" would tell the next
        # agent a repair exists, and one labelled "OPEN" would send it to fix nothing.
        if row.get("fixed_at"):
            return f"{row.get('summary', '')}  [fixed]", _fit(row.get("lesson") or "", width)
        if row.get("invalid_at"):
            why = f"invalid: {row.get('invalid_reason') or ''}"
            if row.get("evidence"):
                why += f" (evidence: {row['evidence']})"
            return f"{row.get('summary', '')}  [invalid]", _fit(why, width)
        return f"{row.get('summary', '')}  [OPEN]", _fit(row.get("lesson") or "", width)
    if table == "items":
        return f"{row.get('id', '')} — {row.get('title', '')}", _fit(row.get("body") or "", width)
    if table == "memories":
        when = (row.get("origin_at") or row.get("at") or "")[:10]
        return f"{when} {row.get('id', '')}".strip(), _fit(row.get("text") or "", width)
    if table == "prompts":
        text = (row.get("text") or "").strip().replace("\n", " ")
        # A note is the AGENT's record of the work — a dead end, a surprise, why it
        # changed approach — and attributing one to the operator would put words in
        # their mouth, which is worse than not surfacing it at all.
        who = {
            "note": "the agent noted:",
            "summary": f"SESSION SUMMARY ({row.get('session', '')}):",
        }.get(row.get("role") or "", "operator asked:")
        return f"{row.get('at', '')[:10]} {who}", _fit(text, width)
    return row.get("id", ""), ""


def _insert_memories(con, state, fts: bool) -> None:
    """LIVE memories only. A forgotten one stays in the log and in `memory list --all`,
    but recall exists to change what an agent does next, and a fact the project has
    stopped believing must not be offered as one."""
    for m in state.memories.values():
        if not m.live:
            continue
        con.execute(
            "insert or replace into memories values(?,?,?,?,?,?,?)",
            (m.id, m.text, json.dumps(m.tags), m.at, m.origin_at, m.by, m.source),
        )
        if fts:
            con.execute("insert into memories_fts values(?,?,?)", (m.id, m.text, " ".join(m.tags)))


#: Where each role's rows sit in a session's `seq` space, so a prompt, a note and the
#: summary never collide on the primary key; the FTS id's letter maps back to it.
_ROLE_OFFSET = {"prompt": 0, "note": 10_000, "summary": 20_000}
_LETTER_OFFSET = {role[0]: off for role, off in _ROLE_OFFSET.items()}
assert len(_LETTER_OFFSET) == len(_ROLE_OFFSET), "two roles share an FTS id letter"


def _insert_sessions(con, state, fts: bool) -> None:
    """Prompts, notes AND the session's summary, in one table, distinguished by `role`.

    A note is what the agent recorded about the work — a dead end, a surprise, why it
    changed approach — and it was folded, replayed and then never indexed, so `recall`
    could not find the one record of why something was abandoned. Notes are shifted by
    10,000 so a note and a prompt with the same `seq` do not collide on the primary key.
    """
    for sess in state.sessions.values():
        entries = [(pr, "prompt") for pr in sess.prompts] + [(nt, "note") for nt in sess.notes]
        if sess.summary:  # what `session end --summary` said the session did (B194)
            entries.append(({"at": sess.ended_at, "text": sess.summary, "seq": 0}, "summary"))
        for n, (entry, role) in enumerate(entries):
            seq = int(entry.get("seq", n))
            con.execute(
                "insert or replace into prompts values(?,?,?,?,?,?)",
                (
                    sess.id,
                    seq + _ROLE_OFFSET[role],
                    # `origin_at` when the note records something that happened before
                    # it was written down -- an imported journal entry or memory --
                    # so `recall` dates it by when it was TRUE, not by when the import
                    # ran and stamped every one of them with today.
                    entry.get("origin_at") or entry.get("at", ""),
                    entry.get("item", ""),
                    entry.get("text", ""),
                    role,
                ),
            )
            if fts:
                con.execute(
                    "insert into prompts_fts values(?,?)",
                    (f"{sess.id}#{role[0]}{seq}", entry.get("text", "")),
                )


def _insert_decisions(con, state, fts: bool) -> None:
    """Decisions + their search index. Split out of `rebuild` because the per-table
    inserts are independent and reading six of them in one function obscured that."""
    for dc in state.decisions.values():
        con.execute(
            "insert or replace into decisions values(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                dc.id,
                dc.title,
                dc.context,
                dc.decision,
                dc.consequences,
                dc.alternatives,
                json.dumps(dc.globs),
                json.dumps(dc.tags),
                dc.status,
                dc.decided_by,
                dc.superseded_by,
                dc.at,
                dc.item,
            ),
        )
        if fts:
            con.execute(
                "insert into decisions_fts values(?,?,?,?,?,?)",
                (dc.id, dc.title, dc.context, dc.decision, dc.consequences, dc.alternatives),
            )


def similar_records(state: State) -> list[dict[str, str]]:
    """Every record a new one could duplicate, as the similarity engine reads them: id,
    kind, title, body, and item (what a bug was filed against, a task's phase).

    Which fields are a record's text is decided here, once, for the stored weights and
    for anyone building a matcher from a State. Removed items and forgotten memories
    are out, as they are out of the rest of the index; closed and invalid records stay
    in -- a new bug that repeats a fixed one is exactly what an add should be shown.
    """
    out = [
        {"id": it.id, "kind": it.kind, "title": it.title, "body": it.body, "item": it.parent}
        for it in state.items.values()
        if not it.removed
    ]
    out += [
        {"id": b.id, "kind": "bug", "title": b.title, "body": b.summary, "item": b.item}
        for b in state.bugs.values()
    ]
    out += [
        {
            "id": ls.id,
            "kind": "lesson",
            "title": ls.title,
            "body": "\n".join(x for x in (ls.rule, ls.why, ls.how) if x),
            "item": "",
        }
        for ls in state.lessons.values()
    ]
    out += [
        {
            "id": d.id,
            "kind": "decision",
            "title": d.title,
            "body": "\n".join(x for x in (d.context, d.decision) if x),
            "item": d.item,
        }
        for d in state.decisions.values()
    ]
    out += [
        {
            "id": r.id,
            "kind": "research",
            "title": r.question,
            "body": "\n".join(x for x in (r.claim, r.mechanism) if x),
            "item": r.item,
        }
        for r in state.research.values()
    ]
    out += [
        {"id": m.id, "kind": "memory", "title": "", "body": m.text, "item": ""}
        for m in state.memories.values()
        if m.live
    ]
    return out


def _insert_similar(con, records: list[dict[str, str]]) -> None:
    """The similarity engine's index (``services.similar``): one row per record, and
    per term its postings -- record ordinals and TF-IDF weights, packed. A term's
    document frequency is its postings' length, so the IDF is stored with them."""
    con.executemany(
        "insert into similar_docs values(?,?,?,?,?)",
        [
            (i, r["id"], r["kind"], r["item"], textsim.digest(r["title"], r["body"]))
            for i, r in enumerate(records)
        ],
    )
    _df, post = textsim.invert([textsim.tokens(r["title"], r["body"]) for r in records])
    con.executemany(
        "insert into similar_terms values(?,?,?)",
        [(t, d.tobytes(), w.tobytes()) for t, (d, w) in post.items()],
    )
    con.execute(
        "insert or replace into meta values('similar_n', ?), ('similar_version', ?)",
        (str(len(records)), str(textsim.VERSION)),
    )


def _insert_bugs(con, state, fts: bool) -> None:
    for bg in state.bugs.values():
        if fts:
            con.execute("insert into bugs_fts values(?,?,?)", (bg.id, bg.summary, bg.lesson))
        con.execute(
            "insert or replace into bugs values(?,?,?,?,?,?,?,?,?,?)",
            (
                bg.id,
                bg.item,
                bg.summary,
                bg.found_at,
                bg.fixed_at,
                bg.regression_test,
                bg.lesson,
                bg.invalid_at,
                bg.invalid_reason,
                bg.evidence,
            ),
        )

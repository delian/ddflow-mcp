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
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ddflow import FORMAT_LEVEL, __version__

from ..config import Config
from ..core import digest as D
from ..core import participation as PA
from ..core import porter, textsim
from ..core.model import State, fold
from ..core.rank import TRIGRAM_MIN, bm25, fuse, rerank, substring_bm25
from ..core.textcut import clip
from ..infra.log import EventLog, _flock

SCHEMA = 11

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

#: The incremental projector's verification marks (snap-perf 3.3): `mark` records the
#: per-shard consumed byte offsets, `shard_digests` the sha256 of each consumed prefix --
#: the store-side copy of the marks `_read_delta` keys the log's own honest reads on.
META_MARK = "mark"
META_SHARD_DIGESTS = "shard_digests"

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
    # the declared record kinds (`core.participation`), one table, `id` = "<kind>:<id>"
    "records": ("title", "body"),
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
        """Bring the projection current with the log. The ONLY write path. The caller
        holds `lock_path`.

        The recorded doctrine used to be "no incremental update, ever": an incremental
        projector is a second implementation of `fold` that can disagree with it. The
        log side found the sandbox where an incremental is safe -- append-only shards,
        prefix hashes, the staleness watermark -- and this is that sandbox, reproduced
        here (snap-perf 3.3, replacing the "never incremental" doctrine with **never
        *unverified* incremental**):

        * the index records what it consumed (per-shard byte offsets and their sha256,
          `META_MARK` / `META_SHARD_DIGESTS`), keyed on the same marks `stale()` and
          `_read_delta` key their honesty on;
        * before a rebuild may reconcile instead of recreate, every consumed prefix is
          re-hashed and must match -- a torn, rewritten or truncated shard is a rebuild;
        * the reconcile writes only the rows the appended deltas changed (the SAME
          projector resolves what a row IS -- one implementation, not two), and any
          doubt of any kind is a full rebuild, never a wrong row.

        The knob (`_incremental_enabled`) turns the shortcut off; the criterion that
        must hold before a rebuild is allowed to be skipped -- the same rows, the same
        meta, and drop-db parity -- is pinned by
        ``tests/test_store.py::test_dropping_the_db_changes_nothing``.
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
        if not self._incremental_enabled():
            # The knob is off: a rebuild recreates the index from nothing. `_build_into`
            # digests the marks for the fresh index below; the reconcile shortcut is not
            # taken at all, with or without stored marks.
            marks = None
        else:
            stored = self._load_shard_digests()
            verified = self._verified_shard_marks(stored, log) if stored is not None else None
            if stored is not None and verified is None:
                # The stored prefixes no longer re-hash: mismatch is a REBUILD. `None` is
                # the revoked licence -- the recreated index below gets freshly digested
                # marks -- never a reconcile keyed on what just failed to verify.
                marks = None
            elif verified is not None:
                marks = verified
            else:
                # First build, or an index written before the marks existed: verify the
                # shards afresh -- nothing was consumed yet, so there is no prefix to doubt.
                # A `None` here is doubt all the same: it stays a rebuild, not an empty
                # licence (`or {}` would turn an unreadable shard into marks-verified).
                marks = self._verified_shard_marks({}, log)
        if marks is None:
            # Every path that revoked the licence above -- the knob off, a mismatch,
            # a first build under doubt -- still needs the marks the fresh index will
            # claim. Taken BEFORE the read, like the fingerprint: marked afterwards, a
            # byte the projection never folded could be claimed consumed the same way.
            marks = self._verified_shard_marks({}, log) or {}
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
        if marks is not None:
            try:
                return self._reconcile(state, events, fingerprint, marks)
            except Exception:
                pass  # any doubt is a rebuild: reconcile aborted, recreate below
        # A name of its own all the same: the lock is advisory, and a temp file that
        # nothing else can name cannot be unlinked or published by anything else.
        tmp = self.path.with_name(
            f"{self.path.name}-rebuilding.{os.getpid()}.{secrets.token_hex(4)}"
        )
        try:
            return self._build_into(tmp, state, events, fingerprint, marks)
        finally:
            for suf in ("", "-wal", "-shm"):
                tmp.with_name(tmp.name + suf).unlink(missing_ok=True)

    def _build_into(
        self, tmp: Path, state: State, events: list, fingerprint, marks: dict[str, tuple[int, str]]
    ) -> State:
        """Write the projection into `tmp` and publish it over the index."""
        con = sqlite3.connect(tmp, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("pragma journal_mode=WAL")
        try:
            self.init(con)
            con.execute("BEGIN IMMEDIATE")
            _write_rows(con, _project(state), self.fts)
            if self.trigram:
                _fill_trigram(con)
            _insert_similar(con, similar_records(state))
            self._write_meta(con, len(events), fingerprint, marks)
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

    def _incremental_enabled(self) -> bool:
        """The knob (r-everything-configurable): may the rebuild reconcile the index with
        the appended deltas instead of recreating it? Off via `DDFLOW_STORE_INCREMENTAL=0`
        for one process, or a `[store].incremental = false` config section when one exists;
        on by default. The verification the shortcut is keyed on is not a knob: no doubt
        is ever skipped, with or without it."""
        raw = os.environ.get("DDFLOW_STORE_INCREMENTAL")
        if raw is not None:
            return raw.strip().lower() not in ("0", "false", "off", "no")
        sec = getattr(self.cfg, "store", None)
        if sec is not None:
            return bool(getattr(sec, "incremental", True))
        return True

    def _load_shard_digests(self) -> dict[str, tuple[int, str]] | None:
        """The per-shard `(consumed bytes, sha256)` of the projection the EXISTING index
        holds, or `None` when there is none (no index, or one written before the marks
        existed). Any incoherence -- rows missing, offsets disagreeing with the digests --
        is `None`: the caller falls back to a full build, which re-records them."""
        if not self.path.exists():
            return None
        try:
            with closing(self.connect()) as con:
                rows = {
                    r["k"]: r["v"]
                    for r in con.execute(
                        "select k, v from meta where k in (?, ?)", (META_MARK, META_SHARD_DIGESTS)
                    )
                }
        except sqlite3.DatabaseError:
            return None
        try:
            offsets = dict(json.loads(rows[META_MARK]))
            digests = json.loads(rows[META_SHARD_DIGESTS])
            if set(offsets) != set(digests):
                return None
            stored: dict[str, tuple[int, str]] = {}
            for name, spec in digests.items():
                size_s, slash, hexed = str(spec).partition("/")
                if not slash or not size_s.isdigit() or offsets.get(name) != int(size_s):
                    return None
                stored[name] = (int(size_s), hexed)
            return stored or None
        except (TypeError, ValueError, KeyError, AttributeError):
            return None

    def _verified_shard_marks(
        self, stored: dict[str, tuple[int, str]], log: EventLog
    ) -> dict[str, tuple[int, str]] | None:
        """Verify every consumed prefix is still the exact bytes the projection was built
        from, and return the marks the NEXT projection is about to consume: per shard, its
        byte length and the sha256 of those bytes, extended from `stored`. `None` on any
        doubt -- a shard that shrank, one rewritten in place, one vanished, one added --
        and a `None` is a full rebuild. The store-side copy of what `_read_delta` checks
        before reusing a parse; here it licenses reconciling instead of recreating."""
        out: dict[str, tuple[int, str]] = {}
        for p in log.shards():
            try:
                data = p.read_bytes()
            except OSError:
                return None
            if p.name in stored:
                size, want = stored[p.name]
                if len(data) < size:
                    return None
                h = D.hasher(data[:size])
                if h.hexdigest() != want:
                    return None
                h.update(data[size:])
                out[p.name] = (len(data), h.hexdigest())
            else:
                # A new shard: nothing of it is consumed yet, so nothing to verify.
                out[p.name] = (len(data), D.hasher(data).hexdigest())
        if set(stored) - set(out):
            return None  # a shard the projection consumed is gone
        return out

    def _reconcile(
        self, state: State, events: list, fingerprint, marks: dict[str, tuple[int, str]]
    ) -> State:
        """Bring the EXISTING index current for the appended deltas, in one transaction:
        the one projector (`_project`) resolves what every row is -- never a second
        implementation of `fold` -- and only the rows the tail changed are written. A
        count that disagrees after the writes aborts the transaction, which the caller
        answers with a full rebuild."""
        con = self.connect()
        try:
            try:
                con.execute("BEGIN IMMEDIATE")
                _reconcile_tables(con, _project(state), self.fts, self.trigram)
                records = similar_records(state)
                # The identity as well as the digested text: an id that changed under
                # content the digest cannot see (a rename kept title and body) must still
                # touch the rows, or a stale id survives a change that "sameness" missed.
                if [tuple(r) for r in con.execute("select kind, item, id, digest from similar_docs order by ord")] != [
                    (r["kind"], r["item"], r["id"], textsim.digest(r["title"], r["body"]))
                    for r in records
                ]:
                    _insert_similar(con, records, clear=True)
                self._write_meta(con, len(events), fingerprint, marks)
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
        finally:
            con.close()
        return state

    def _write_meta(
        self,
        con,
        n_events: int,
        fingerprint,
        marks: dict[str, tuple[int, str]],
    ) -> None:
        """The meta both writers leave behind -- the figures `stale()` compares its next
        cheap read against, and the verification marks the next rebuild must re-earn before
        it may reconcile. `marks` holds what was verified (or, on a full build, digested
        fresh) with the shards as they were when they were marked, at or before the read,
        so they may only exceed the fingerprint, the safe direction."""
        con.execute("insert or replace into meta values('events', ?)", (str(n_events),))
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
        con.execute(
            "insert or replace into meta values(?, ?)",
            (META_MARK, json.dumps({name: pair[0] for name, pair in sorted(marks.items())})),
        )
        con.execute(
            "insert or replace into meta values(?, ?)",
            (
                META_SHARD_DIGESTS,
                json.dumps(
                    {name: f"{size}/{hexed}" for name, (size, hexed) in sorted(marks.items())},
                    sort_keys=True,
                ),
            ),
        )

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
        self,
        table: str,
        query: str,
        limit: int = 5,
        *,
        rerank_by_likeness: bool = False,
        prefix: str = "",
    ) -> list[dict[str, Any]]:
        """Rank ``table`` against ``query``: BM25 over the porter words fused (reciprocal
        rank) with a trigram BM25 over the substrings, best first.

        Both lists exist on every machine. Where SQLite has FTS5 they come from its indexes;
        where it has none (or no trigram tokenizer) the same lists are computed in Python
        over the same columns and the same query terms. Both lists are identical in their
        candidates everywhere: the substring list by construction, the WORD list because the
        fallback splits words as FTS5's unicode61 tokenizer does (`textsim.fts_words`) and
        stems them as its porter tokenizer does (`core.porter`), each checked against FTS5 (for ASCII,
        Latin and the common scripts; see `textsim.fts_words` for what can still differ).
        ``rerank_by_likeness`` additionally reorders the fused top by fuzzy likeness to the
        query (`rank.rerank`: rapidfuzz when installed, else difflib).

        The user query is passed through ``_fts_query``, which strips FTS operator
        characters and ORs the terms. Raw user text reaching an FTS5 MATCH is a syntax
        error waiting to happen — an unbalanced quote or a bare ``-`` raises, and a
        search that throws on a normal question is a search nobody uses.

        ``prefix`` keeps only the rows whose id starts with it (the declared kinds share
        the ``records`` table, ids ``<kind>:<id>``), so one kind is ranked on its own and a
        large one cannot crowd out another.
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
                    rows = [r for r in rows if str(r.get("id", "")).startswith(prefix)]
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
                lists = [self._fts_ranking(con, f"{table}_fts", _fts_query(query), pool, prefix)]
                lists.append(
                    self._fts_ranking(con, f"{table}_tri", _tri_query(terms), pool, prefix)
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
    def _fts_ranking(con, index: str, match: str, limit: int, prefix: str = "") -> list[str]:
        """Ids of the FTS5 table ``index`` matching ``match``, best BM25 first; empty for no
        expression, no such table or one FTS5 refuses. ``prefix`` keeps ids that start with it."""
        if not match:
            return []
        try:
            rows = con.execute(
                # bandit B608: `index` is `<key of _SEARCH_COLS>_fts` or `_tri`, built here.
                f"select id from {index} where {index} match ? "  # nosec B608
                f"and substr(id, 1, ?) = ? order by bm25({index}), id limit ?",
                (match, len(prefix), prefix, limit),
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
                seq = _role_seq(role, int(digits or 0))
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
    return f"{row.get('session', '')}#{role[0]}{_seq_role(role, row.get('seq', 0))}"


def _fill_trigram(con) -> None:
    """Copy each table's ranked text from its porter FTS5 table into the trigram one."""
    for table, _cols in _SEARCH_COLS.items():
        con.execute(
            # bandit B608: names come from the fixed `_SEARCH_COLS`.
            f"insert into {table}_tri(id, text) select id, {_TRI_EXPR[table]} from {table}_fts"  # nosec B608
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


def recall_sources() -> tuple[tuple[str, str, str], ...]:
    """What recall searches now: the fixed sources, then each declared kind (its name is its
    table, its label the name in capitals, its hint the descriptor's ``recall_hint``)."""
    return RECALL_SOURCES + PA.recall_entries()


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
    if table in PA.DECLARED:
        status = row.get("status") or ""
        tag = f"  [{status}]" if status and status != "active" else ""
        return f"{row.get('title', '')}{tag}", _fit(row.get("body") or "", width)
    return row.get("id", ""), ""


@dataclass(frozen=True)
class _Row:
    """One projected row: its primary key, its values (the insert's arguments) and its
    FTS5 row when its table has one. The full writer inserts every row; the reconciler
    differs them -- residues ONE shape, so the two never disagree."""

    key: tuple
    values: tuple
    fts: tuple | None = None


@dataclass(frozen=True)
class _Table:
    """One projected table: its insert statements and how its rows are keyed. `key_len`
    leading columns are the primary key (0 for `cadences`, whose identity is the whole
    row; 2 for the `(session, seq)` of `prompts`), and a 1-keyed table's key IS its FTS
    id, which is what an FTS/trigram delete needs (prompts' is not, so its old FTS rows
    cannot stay behind by deletion -- and they never need to: prompts only ever grow)."""

    name: str
    cols: tuple[str, ...]
    key_len: int
    fts: str | None
    fts_w: int

    @property
    def insert(self) -> str:
        return f"insert or replace into {self.name} values({','.join('?' * len(self.cols))})"

    @property
    def fts_insert(self) -> str:
        return f"insert or replace into {self.fts} values({','.join('?' * self.fts_w)})"

    def where(self, key: tuple) -> str:
        cols = self.cols[: self.key_len or len(self.cols)]
        return f"delete from {self.name} where {' and '.join(c + ' = ?' for c in cols)}"  # nosec B608


#: Every projected table, in ITS insert order (`SCHEMA`'s column order, byte-faithful).
_TABLES = {
    "items": _Table(
        "items",
        (
            "id",
            "kind",
            "title",
            "parent",
            "state",
            "needs",
            "globs",
            "tags",
            "priority",
            "body",
            "worktree",
            "branch",
            "merged_sha",
            "blocked_reason",
            "lease_holder",
            "lease_expires",
            "gates",
            "created_at",
            "completed_at",
        ),
        1,
        "items_fts",
        4,
    ),
    "lessons": _Table(
        "lessons",
        ("id", "title", "rule", "why", "how", "seen_in", "tags", "at", "superseded_by"),
        1,
        "lessons_fts",
        6,
    ),
    "research": _Table(
        "research",
        ("id", "question", "claim", "verdict", "probe", "sources", "at", "item"),
        1,
        "research_fts",
        5,
    ),
    "decisions": _Table(
        "decisions",
        (
            "id",
            "title",
            "context",
            "decision",
            "consequences",
            "alternatives",
            "globs",
            "tags",
            "status",
            "decided_by",
            "superseded_by",
            "at",
            "item",
        ),
        1,
        "decisions_fts",
        6,
    ),
    "memories": _Table(
        "memories",
        ("id", "text", "tags", "at", "origin_at", "by", "source"),
        1,
        "memories_fts",
        3,
    ),
    "bugs": _Table(
        "bugs",
        (
            "id",
            "item",
            "summary",
            "found_at",
            "fixed_at",
            "regression_test",
            "lesson",
            "invalid_at",
            "invalid_reason",
            "evidence",
        ),
        1,
        "bugs_fts",
        3,
    ),
    "prompts": _Table(
        "prompts",
        ("session", "seq", "at", "item", "text", "role"),
        2,
        "prompts_fts",
        2,
    ),
    "records": _Table(
        "records",
        ("id", "kind", "rid", "title", "body", "status", "at"),
        1,
        "records_fts",
        3,
    ),
    "cadences": _Table("cadences", ("name", "at", "by", "result"), 0, None, 0),
}


def _rows_items(rows: dict[str, list[_Row]], state: State) -> None:
    for it in state.items.values():
        if it.removed:
            continue
        rows["items"].append(
            _Row(
                (it.id,),
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
                (it.id, it.title, it.body, " ".join(it.tags)),
            )
        )


def _rows_lessons(rows: dict[str, list[_Row]], state: State) -> None:
    # A contested lesson (B230e852e10) is not advice until `resolve` settles it.
    for ls in (x for x in state.lessons.values() if not x.contested):
        rows["lessons"].append(
            _Row(
                (ls.id,),
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
                (ls.id, ls.title, ls.rule, ls.why, ls.how, " ".join(ls.tags)),
            )
        )


def _rows_research(rows: dict[str, list[_Row]], state: State) -> None:
    for rn in state.research.values():
        rows["research"].append(
            _Row(
                (rn.id,),
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
                (rn.id, rn.question, rn.claim, rn.probe, rn.verdict),
            )
        )


def _rows_decisions(rows: dict[str, list[_Row]], state: State) -> None:
    for dc in state.decisions.values():
        rows["decisions"].append(
            _Row(
                (dc.id,),
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
                (dc.id, dc.title, dc.context, dc.decision, dc.consequences, dc.alternatives),
            )
        )


def _rows_memories(rows: dict[str, list[_Row]], state: State) -> None:
    # LIVE memories only: a forgotten one stays in the log and in `memory list --all`,
    # but recall exists to change what an agent does next.
    for m in state.memories.values():
        if not m.live:
            continue
        rows["memories"].append(
            _Row(
                (m.id,),
                (
                    m.id,
                    m.text,
                    json.dumps(m.tags),
                    m.at,
                    m.origin_at,
                    m.by,
                    m.source,
                ),
                (m.id, m.text, " ".join(m.tags)),
            )
        )


def _rows_bugs(rows: dict[str, list[_Row]], state: State) -> None:
    for bg in state.bugs.values():
        rows["bugs"].append(
            _Row(
                (bg.id,),
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
                (bg.id, bg.summary, bg.lesson),
            )
        )


def _rows_prompts(rows: dict[str, list[_Row]], state: State) -> None:
    """Prompts, notes AND the session's summary, one table, distinguished by `role`;
    each role's ``seq`` sits in a band the others cannot reach (`_role_seq`), so the
    session's own seqs can never collide on the primary key (the FTS id's letter
    maps back)."""
    for sess in state.sessions.values():
        entries = [(pr, "prompt") for pr in sess.prompts] + [(nt, "note") for nt in sess.notes]
        if sess.summary:  # what `session end --summary` said the session did (B194)
            entries.append(({"at": sess.ended_at, "text": sess.summary, "seq": 0}, "summary"))
        for n, (entry, role) in enumerate(entries):
            seq = _role_seq(role, int(entry.get("seq", n)))
            rows["prompts"].append(
                _Row(
                    (sess.id, seq),
                    (
                        sess.id,
                        seq,
                        # `origin_at` when the note records something that happened before
                        # it was written down -- so `recall` dates it by when it was TRUE.
                        entry.get("origin_at") or entry.get("at", ""),
                        entry.get("item", ""),
                        entry.get("text", ""),
                        role,
                    ),
                    (f"{sess.id}#{role[0]}{int(entry.get('seq', n))}", entry.get("text", "")),
                )
            )


def _rows_records(rows: dict[str, list[_Row]], state: State) -> None:
    """The ACTIVE records of the declared kinds that are recalled, in the ``records``
    table: a retired, superseded or merged one no longer changes what an agent should
    do. The id is ``<kind>:<id>``."""
    for rec in state.defs.values():
        if not rec.live:
            continue
        for d in PA.recalled():
            if d.def_kind != rec.kind:
                continue
            key = f"{d.name}:{rec.id}"
            title = str(rec.fields.get(d.title, ""))
            body = d.text({k: v for k, v in rec.fields.items() if k != d.title})
            rows["records"].append(
                _Row(
                    (key,),
                    (key, d.name, rec.id, title, body, rec.status, rec.updated_at or rec.at),
                    (key, title, body),
                )
            )


def _rows_cadences(rows: dict[str, list[_Row]], state: State) -> None:
    for name, runs in state.cadences.items():
        for r in runs:
            rows["cadences"].append(_Row((), (name, r["at"], r["by"], r["result"]), None))


def _project(state: State) -> dict[str, list[_Row]]:
    """Every table's rows as they are resolved THE one time: `_write_rows` inserts them
    whole, `_reconcile_tables` inserts only what differs. ONE projector means an
    incremental never disagrees with a rebuild -- it is the rebuild, minus the rows
    that were already there."""
    rows: dict[str, list[_Row]] = {name: [] for name in _TABLES}
    _rows_items(rows, state)
    _rows_lessons(rows, state)
    _rows_research(rows, state)
    _rows_decisions(rows, state)
    _rows_memories(rows, state)
    _rows_bugs(rows, state)
    _rows_prompts(rows, state)
    _rows_records(rows, state)
    _rows_cadences(rows, state)
    return rows


def _write_rows(con, proj: dict[str, list[_Row]], fts: bool) -> None:
    """The full writer: every projected row, base and FTS, into a fresh index."""
    for name, rows in proj.items():
        t = _TABLES[name]
        for r in rows:
            con.execute(t.insert, r.values)
            if fts and r.fts is not None:
                con.execute(t.fts_insert, r.fts)


#: The ranked text of a trigram row, per table: the SAME columns `_SEARCH_COLS` ranks on,
#: joined -- `_fill_trigram` copies them all, a reconcile only the changed ids.
_TRI_EXPR = {
    t: " || ' ' || ".join(f"coalesce({c}, '')" for c in cols) for t, cols in _SEARCH_COLS.items()
}


def _reconcile_tables(con, proj: dict[str, list[_Row]], fts: bool, trig: bool) -> None:
    """The incremental writer: read the index's rows once, write only what differs.
    Raises `RuntimeError` on a count that disagrees -- the caller answers with a full
    rebuild, never with a wrong row."""
    for name, rows in proj.items():
        t = _TABLES[name]
        cols = ", ".join(t.cols)  # nosec B608: fixed at import
        want = {r.key: r for r in rows} if t.key_len else {r.values: r for r in rows}
        have = {
            (tuple(r)[: t.key_len] if t.key_len else tuple(r)): tuple(r)
            for r in con.execute(f"select {cols} from {t.name}")  # nosec B608
        }
        touched: list[_Row] = []
        gone: list[tuple] = []
        for key, r in want.items():
            if have.get(key) != r.values:
                con.execute(t.insert, r.values)
                if fts and r.fts is not None:
                    # FTS5 replaces on rowid, which a text id never equals -- a plain
                    # insert-or-replace would append a duplicate row for the same id.
                    # Delete by the id first (the arity is irrelevant: the id column IS
                    # r.fts[0]); the row just written replaces.
                    con.execute(f"delete from {t.fts} where id = ?", (r.fts[0],))  # nosec B608
                    con.execute(t.fts_insert, r.fts)
                touched.append(r)
        for key in set(have) - set(want):
            con.execute(t.where(key), key)
            if fts and t.fts is not None and t.key_len == 1:
                # A 1-keyed table's key IS its FTS id; prompts' (key_len 2) is not,
                # and prompts only ever grow, so a stale FTS row there cannot be left
                # behind -- no gone prompt has a key-deletable FTS row.
                con.execute(f"delete from {t.fts} where id = ?", (key[0],))  # nosec B608
            gone.append(key)
        if t.name == "prompts" and gone:
            # A gone prompt disobeys "prompts only ever grow" -- its stale FTS/trigram
            # row cannot be deleted by base key -- and doubt is a rebuild, not a residue.
            raise RuntimeError(f"incremental {t.name}: a projected prompt row is gone")
        if con.execute(f"select count(*) from {t.name}").fetchone()[0] != len(rows):  # nosec B608
            raise RuntimeError(f"incremental {t.name}: reconciled rows disagree")
        if not trig or not t.fts:
            continue
        if gone and t.key_len == 1:
            for chunk in _chunks([k[0] for k in gone], _RECONCILE_BATCH):
                phs = ",".join("?" * len(chunk))
                con.execute(
                    f"delete from {t.name}_tri where id in ({phs})",  # nosec B608
                    chunk,
                )
        ids = [r.fts[0] for r in touched if r.fts is not None]
        for chunk in _chunks(ids, _RECONCILE_BATCH):
            phs = ",".join("?" * len(chunk))
            con.execute(
                f"delete from {t.name}_tri where id in ({phs})",  # nosec B608
                chunk,
            )
            con.execute(
                "insert into "
                f"{t.name}_tri(id, text) select id, {_TRI_EXPR[t.name]} "
                f"from {t.fts} where id in ({phs})",  # nosec B608
                chunk,
            )


def _chunks(items: list, n: int) -> list:
    """``items`` in slices of ``n``, so an ``id in (?, ...)`` list stays far under
    SQLite's bound-variable limit (999 on older builds): a delta large enough to
    exceed it batches, instead of the transaction dying mid-reconcile."""
    return [items[i : i + n] for i in range(0, len(items), n)]


#: Row cap per ``id in (...)`` clause in a reconcile (``_RECONCILE_BATCH``):
#: 900 bounds the variables below any SQLite build's limit; a bigger delta just
#: batches. A batch that FAILED would answer the whole reconcile with a rebuild.
_RECONCILE_BATCH = 900


#: Two roles' rows sit in `seq` bands no other role can reach, however many entries
#: one session holds: a prompt's seq IS its count (>= 0, what `sessions.start`
#: assigns), a note's is its count, so notes sit at -(seq+1) and the summary at one
#: fixed negative slot -- a prompt, a note and the summary can never collide on the
#: primary key at any size a log fits on. The FTS id's letter maps the row back
#: (`_seq_role`), which is what makes the bands invertible instead of shifted.
_SUMMARY_SLOT = 1 << 62  # negative for the summary; 2**62 notes away from every note


def _role_seq(role: str, seq: int) -> int:
    """The ``seq`` a ``prompts`` row keeps, from the entry's own seq and its role."""
    if role == "note":
        return -(int(seq) + 1)
    if role == "summary":
        return int(seq) - _SUMMARY_SLOT
    return int(seq)


def _seq_role(role: str, seq: int) -> int:
    """The inverse of ``_role_seq``: the seq an FTS id's digits carried."""
    if role == "note":
        return -(int(seq) + 1)
    if role == "summary":
        return int(seq) + _SUMMARY_SLOT
    return int(seq)


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
        if not getattr(ls, "contested", None)
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
        if not getattr(d, "contested", None)
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


def _insert_similar(con, records: list[dict[str, str]], clear: bool = False) -> None:
    """The similarity engine's index (``services.similar``): one row per record, and
    per term its postings -- record ordinals and TF-IDF weights, packed. With `clear`
    (a reconcile decided the corpus changed) every previous row is replaced from
    `records`; the caller must pass the COMPLETE current record set for that -- a
    partial list would wipe the unchanged records' postings with reclaiming."""
    if clear:
        con.execute("delete from similar_docs")
        con.execute("delete from similar_terms")
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

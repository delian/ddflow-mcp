"""The projection half of `Store`: rebuild, incremental reconcile, publish, ensure."""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

from ...core import digest as D
from ...core import textsim
from ...core.model import State, fold
from ..log import EventLog, _flock
from ._fingerprint import (
    _INDEX_FILE,
    META_MARK,
    META_SHARD_DIGESTS,
    REBUILD_TEMP_MAX_AGE_S,
    _capability_tables,
    _environmental,
    collect_over_cap,
)
from ._tables import (
    _fill_trigram,
    _insert_similar,
    _project,
    _reconcile_tables,
    _write_rows,
    similar_records,
)


class _ProjectionMixin:
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
        with _flock(self.lock_path, sys.modules[__package__].REBUILD_LOCK_TIMEOUT_S):
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
        reconciling = True  # revoked the moment any path above doubts the licence
        if not self._incremental_enabled():
            # The knob is off: a rebuild recreates the index from nothing. `_build_into`
            # digests the marks for the fresh index below; the reconcile shortcut is not
            # taken at all, with or without stored marks.
            marks = None
            reconciling = False
        else:
            stored = self._load_shard_digests()
            verified = self._verified_shard_marks(stored, log) if stored is not None else None
            if stored is not None and verified is None:
                # The stored prefixes no longer re-hash: mismatch is a REBUILD. `None` is
                # the revoked licence -- the recreated index below gets freshly digested
                # marks -- never a reconcile keyed on what just failed to verify.
                marks = None
                reconciling = False
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
            marks = self._verified_shard_marks({}, log)
        if reconciling and self._capability_changed():
            # An index built on a different capability (FTS5 or the trigram tokenizer on
            # or off) cannot be reconciled into this one: the tables the fresh index needs
            # were never created, so a delta over the old one would leave them missing or
            # empty, and the meta would then claim they exist. A capability change is a
            # full rebuild, exactly as `stale()` already decides for the trigram flip.
            reconciling = False
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
        if reconciling and marks is not None:
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
        self._publish(tmp)
        return state

    def _publish(self, tmp: Path) -> None:
        """Publish `tmp` over the index atomically: readers see either the old index or
        the new one, never a half-built one. WAL sidecars are removed first so the
        replaced file is self-contained. The one swap both writers (`_build_into` and
        `_reconcile`) end with, each into a fresh file, never the live one -- the
        snapshot contract ``StoreIndex``'s read-only connection depends on."""
        for suf in ("-wal", "-shm"):
            tmp.with_name(tmp.name + suf).unlink(missing_ok=True)
            self.path.with_name(self.path.name + suf).unlink(missing_ok=True)
        tmp.replace(self.path)
        self._collect_stale_indexes()

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

    def _capability_changed(self) -> bool:
        """Does the existing index not actually hold the capability this store needs?
        FTS5 on/off or the trigram tokenizer on/off disagree with the index's `meta`, OR
        the meta claims the capability while its tables are absent -- in either case the
        reconcile delta would write a table the old index never created, leaving it empty
        while the meta claims it is there. A capability change is a full rebuild, never a
        delta over the gap; `stale()` reads the same two facts the same way."""
        if not self.path.exists():
            return False
        try:
            with closing(self.connect()) as con:
                meta = {
                    r["k"]: r["v"]
                    for r in con.execute("select k, v from meta where k in ('fts', 'trigram')")
                }
                if not _capability_tables(con, self.fts, self.trigram):
                    return True
        except sqlite3.DatabaseError:
            return True  # an unreadable meta is doubt, and doubt is a rebuild
        return meta.get("fts", "0") != ("1" if self.fts else "0") or meta.get("trigram", "0") != (
            "1" if self.trigram else "0"
        )

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
        """Bring a COPY of the current index current for the appended deltas, in one
        transaction, then swap the copy over the index like `_build_into` does. The one
        projector (`_project`) resolves what every row is -- never a second implementation
        of `fold` -- and only the rows the tail changed are written. The copy in between is
        a raw SQLite backup (pages, not row re-inserts), so the incremental saving
        survives. The swap IS the point: the live index is never edited in place, which is
        the snapshot contract ``StoreIndex``'s read-only connection depends on -- an
        in-place reconcile handed a reader opened before the postings whose doc
        indices outrange the docs list it read at open (Bba4a45036a). A count that
        disagrees after the writes aborts the transaction, which the caller answers with a
        full rebuild."""
        tmp = self.path.with_name(
            f"{self.path.name}-rebuilding.{os.getpid()}.{secrets.token_hex(4)}"
        )
        try:
            with closing(self.connect()) as src:
                dst = sqlite3.connect(tmp, isolation_level=None)
                try:
                    src.backup(dst)
                finally:
                    dst.close()
            dst = sqlite3.connect(tmp, isolation_level=None)
            dst.row_factory = sqlite3.Row
            try:
                try:
                    dst.execute("BEGIN IMMEDIATE")
                    _reconcile_tables(dst, _project(state), self.fts, self.trigram)
                    records = similar_records(state)
                    # The identity as well as the digested text: an id that changed under
                    # content the digest cannot see (a rename kept title and body) must still
                    # touch the rows, or a stale id survives a change that "sameness" missed.
                    if [
                        tuple(r)
                        for r in dst.execute(
                            "select kind, item, id, digest from similar_docs order by ord"
                        )
                    ] != [
                        (r["kind"], r["item"], r["id"], textsim.digest(r["title"], r["body"]))
                        for r in records
                    ]:
                        _insert_similar(dst, records, clear=True)
                    self._write_meta(dst, len(events), fingerprint, marks)
                    dst.execute("COMMIT")
                except BaseException:
                    dst.execute("ROLLBACK")
                    raise
            finally:
                dst.close()
            self._publish(tmp)
        finally:
            for suf in ("", "-wal", "-shm"):
                tmp.with_name(tmp.name + suf).unlink(missing_ok=True)
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
        """Delete the indexes of OTHER code untouched for `[store].stale_index_days`. Best effort,
        and never this fingerprint's own files: an index in use is touched by `stale()` at least
        daily, and a mistaken removal only costs that checkout a rebuild."""
        gone: list[Path] = []
        mine = self.path.name
        cutoff = time.time() - self.cfg.store.stale_index_days * 86400
        for f in self.path.parent.glob("index.db*"):
            if f.name.startswith(mine) or not _INDEX_FILE.match(f.name):
                continue
            with contextlib.suppress(OSError):
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    gone.append(f)
        gone += collect_over_cap(self.path.parent, mine)
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

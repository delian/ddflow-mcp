"""The read and on-disk-snapshot half of ``EventLog`` (``_ReadMixin``)."""

from __future__ import annotations

import bisect
import contextlib
import itertools
import json
import marshal
import os
import re
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ...core import digest as D
from ...core.events import TAIL_MAX_BYTES, TAIL_WINDOW_BYTES, Event
from .. import fsio
from ._parse import (
    _EVENT_FIELDS,
    _MAX_INCREMENTAL,
    _MERGED,
    _ORDER_ARITY,
    _PARSE_CACHE,
    _SNAP_COVERED,
    _SNAP_ORDER,
    _SNAP_SWEPT,
    _SNAP_SWEPT_MAX,
    _SNAPSHOT_SLOT,
    _SNAPSHOTS,
    SNAPSHOT_ENV,
    SNAPSHOT_FILE,
    SNAPSHOT_FORMAT,
    _cached_events,
    _Delta,
    _digest,
    _event_tuple,
    _gated_read,
    _license,
    _Merged,
    _parse_lines,
    _Parsed,
    _parser_stamp,
    _snapshot_frame,
    _snapshot_objects,
    _sorted_unique,
)
from ._skew import running_version


class _ReadMixin:
    # -- reading -------------------------------------------------------------------
    def _read_shard(self, path: Path) -> tuple[list[Event], int]:
        """One shard's events (a fresh list) and its unparseable-line count."""
        d = self._read_delta(path)
        if d is None:
            return [], 0
        return d.whole + d.torn, d.skipped

    def _read_delta(self, path: Path, trust_snapshot: bool = True) -> _Delta | None:
        """One shard, re-parsing only what has been APPENDED since last time.

        Parsing dominates a read — 97 ms of 115 ms at 20,000 events — and an
        append-only file guarantees the bytes already parsed have not changed, so the
        tail is the only new work. The guarantee is CHECKED rather than assumed: the
        consumed prefix is re-hashed on every read and any mismatch falls back to a
        full parse. A rewrite, a truncation, a `git checkout`, a `git merge` and a
        delete-and-recreate all land in that one case, so there is no list of mechanisms
        to keep current.

        3.2: the prefix is streamed into the hasher and only the bytes from `consumed`
        on are held (and only they will be parsed), so a read whose cache is warm no
        longer materialises the whole file; a shorter file rejects the cache before any
        read at all. The gate may only reject, never accept: the hash IS the check.

        A trailing fragment with no newline is parsed (so `doctor` keeps reporting a
        torn append) but never marked consumed, so the next call re-reads it and sees
        the completed line.
        """
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            # Vanished between the glob and the read -- another agent's shard removed, or
            # a branch switch. Benign, and the only OSError that is: anything else means
            # the file is THERE and we cannot read it (permissions, a stale NFS handle,
            # a directory where a shard should be), and swallowing that would drop a
            # whole agent's events from the queue while reporting success. Caught by the
            # cross-family critic: the pre-cache code caught `FileNotFoundError` only,
            # and widening it to `OSError` turned a loud failure into a silent one.
            return None
        cached = _PARSE_CACHE.get(path) if self.log_cfg.reuse_parsed else None
        if cached is not None and cached.seeded and not trust_snapshot:
            cached = None  # `verify` re-parses from the bytes: it must not trust a cache
        start, skipped = 0, 0
        hasher = None
        base: _Parsed | None = None
        chunk = b""
        try:
            if cached is None and trust_snapshot and self.log_cfg.reuse_parsed:
                seeded = self._seed_from_snapshot(path, size)
                if seeded is not None:
                    base, hasher, chunk = seeded
                    start, skipped = base.consumed, base.skipped
            if base is None:
                chunk, hasher, start, skipped, base = _license(path, cached, size)
        except FileNotFoundError:
            # Vanished between the stat and the open: the same benign race again.
            return None
        cut = chunk.rfind(b"\n") + 1  # 0 when the tail holds no newline at all
        whole, fragment = chunk[:cut], chunk[cut:]
        fresh, fresh_skipped = _parse_lines(whole)
        skipped += fresh_skipped
        prior = len(base.events) if base is not None else 0
        # The ceiling is GLOBAL -- every shard of every repo this process has read. It is
        # documented as a memory ceiling, and a per-shard limit is not one: a repo with
        # eight agents would hold eight times the promised bound.
        held = _cached_events() - len(cached.events if cached is not None else ())
        parsed: _Parsed | None = None
        kept = (
            self.log_cfg.reuse_parsed
            and held + prior + len(fresh) <= self.log_cfg.max_cached_events
        )
        digest, hasher = self._continue_digest(hasher, chunk, cut, whole)
        if kept:
            if base is not None:
                base.events.extend(fresh)
                base.consumed, base.digest, base.skipped = start + cut, digest, skipped
                parsed = base
            else:
                parsed = _Parsed(start + cut, digest, fresh, skipped, gen=object())
            _PARSE_CACHE[path] = parsed
            events = parsed.events
            stamp = (parsed.consumed, parsed.digest, parsed.skipped)
        else:
            # Over the ceiling (or disabled): do not hold it, and do not leave a STALE
            # entry behind either -- an entry left behind would be served forever.
            _PARSE_CACHE.pop(path, None)
            events = (base.events + fresh) if base is not None else fresh
            # 3.1: not HELD does not mean not PERSISTED; the (consumed, digest, skipped)
            # is exactly what the held branch stores. Persisting and holding are
            # different decisions, and `_maybe_write_snapshot` streams an unheld shard.
            stamp = (start + cut, digest, skipped) if self.log_cfg.reuse_parsed else None
        torn, torn_skipped = _parse_lines(fragment)
        return _Delta(parsed, events, torn, skipped + torn_skipped, stamp)

    @staticmethod
    def _continue_digest(hasher, chunk: bytes, cut: int, whole: bytes) -> tuple[str, Any]:
        """The digest a stored `(consumed, digest)` carries for this read: the hasher
        already streaming over the prefix continues over the appended tail, and a fresh
        parse hashes the chunk it parsed. `hexdigest()` does not finalise a hashlib
        object, so it can still be updated afterwards."""
        if hasher is None:
            return _digest(chunk[:cut]), None
        hasher.update(whole)
        return hasher.hexdigest(), hasher

    # -- on-disk snapshot (B166) ----------------------------------------------------
    def _snapshot_path(self) -> Path:
        """`read-snapshot-<fingerprint>.bin`: the fingerprint is the running version and the
        parser stamp, so code that cannot read another's snapshot never shares its file."""
        stem = SNAPSHOT_FILE.removesuffix(".bin")
        key = D.content_digest(f"{running_version()}/{_parser_stamp()}".encode())[:16]
        return self.dir.parent / "local" / f"{stem}-{key}.bin"

    def _touch_snapshot(self) -> None:
        """Mark the snapshot as used (at most daily): reads never write it, so without this its
        mtime says when it was written and a sibling checkout on other code would collect a
        long-lived reader's snapshot as abandoned."""
        path = self._snapshot_path()
        with contextlib.suppress(OSError):
            if time.time() - path.stat().st_mtime > self.log_cfg.snapshot_touch_s:
                os.utime(path)

    def _collect_stale_snapshots(self, mine: Path, max_bytes: int | None = None) -> None:
        """Remove the snapshots of other code untouched for `[log].snapshot_stale_days`, and the
        pre-fingerprint `read-snapshot.bin` once it is that old; then, while all snapshots
        together exceed `max_bytes` (default `[log].snapshot_max_mb`), the oldest of the
        others. Best effort."""
        stem = SNAPSHOT_FILE.removesuffix(".bin")
        cutoff = time.time() - self.log_cfg.snapshot_stale_days * 86400
        if max_bytes is None:
            max_bytes = self.log_cfg.snapshot_max_mb * 1_000_000
        others: list[tuple[float, int, Path]] = []
        total = 0
        with contextlib.suppress(OSError):
            total = mine.stat().st_size
        for f in mine.parent.glob(f"{stem}*.bin"):
            if f == mine or not re.fullmatch(rf"{re.escape(stem)}(-[0-9a-f]{{16}})?\.bin", f.name):
                continue
            with contextlib.suppress(OSError):
                st = f.stat()
                if not f.is_file():
                    continue
                if st.st_mtime < cutoff:
                    f.unlink()
                else:
                    others.append((st.st_mtime, st.st_size, f))
                    total += st.st_size
        mine_size = total - sum(o[1] for o in others)
        if mine_size > max_bytes:  # the current file alone is over the cap
            with contextlib.suppress(OSError):
                mine.unlink()
                _SNAP_COVERED.pop(self.dir, None)
                total -= mine_size
        for _, size, f in sorted(others):
            if total <= max_bytes:
                break
            with contextlib.suppress(OSError):
                f.unlink(
                    missing_ok=True
                )  # gone already (a sibling collected it): still not counted
                total -= size

    def _discard_disabled_snapshots(self) -> None:
        """`[log].snapshot = false` neither reads nor writes a snapshot, so the files an
        earlier setting left (~113 MB per fingerprint at 137k events) are dead weight: delete
        them, once per process per log. The per-process environment switch never does."""
        if self.log_cfg.snapshot or not self.cache_writes or self.dir in _SNAP_SWEPT:
            return
        if (
            len(_SNAP_SWEPT) >= _SNAP_SWEPT_MAX
        ):  # bounded: a sweep repeated for a forgotten dir is harmless
            _SNAP_SWEPT.clear()
        _SNAP_SWEPT.add(self.dir)
        stem = SNAPSHOT_FILE.removesuffix(".bin")
        for f in (self.dir.parent / "local").glob(f"{stem}*.bin"):
            if re.fullmatch(rf"{re.escape(stem)}(-[0-9a-f]{{16}})?\.bin", f.name):
                with contextlib.suppress(OSError):
                    f.unlink()

    def _snapshot_enabled(self) -> bool:
        # A log built only to read someone else's repository (`cache_writes=False`) neither
        # writes NOR reads a snapshot there: what is in another checkout's `.ddflow/local/`
        # is that checkout's own state, not something to decode into this process.
        return (
            self.log_cfg.reuse_parsed
            and self.log_cfg.snapshot
            and self.cache_writes
            and os.environ.get(SNAPSHOT_ENV) != "0"
        )

    def _load_snapshot(self) -> dict[str, tuple]:
        """The snapshot's per-shard entries, or `{}` -- never an exception, never a guess.

        Trusted only when ALL of: the header parses; its format, ddflow version and Event
        field list are the ones running; the payload's length and sha256 match the header
        (bit rot, truncation, a half-written file); and the payload unmarshals into the
        expected shape. Any doubt is "no snapshot", and the caller parses the log. Even a
        snapshot that passes is only a CLAIM about shard prefixes: `_seed_from_snapshot`
        re-hashes the real bytes before using any of it.

        What that does NOT prove is that the decoded events are the ones those bytes
        parse to: the shard hashes bind the snapshot to the log, and its own checksum
        guards against rot and truncation, but a hand-crafted file with consistent
        checksums is believed. That is the same trust as the rest of `.ddflow/local/`
        (machine-local state in the operator's own checkout, never merged or fetched), which
        is why a read-only log skips snapshots entirely and `verify()` never uses one.
        `marshal` is used for speed and, like any file in your own checkout, is not a
        place to put untrusted bytes.
        """
        memo = _SNAPSHOTS.get(self.dir)
        if memo is not None:
            return memo
        entries: dict[str, tuple] = {}
        _SNAPSHOTS[self.dir] = entries
        _SNAP_COVERED[self.dir] = 0  # what is covered is what gets seeded from THIS file
        try:
            raw = self._snapshot_path().read_bytes()
            head, _, payload = raw.partition(b"\n")
            meta = json.loads(head)
            if (
                meta["format"] != SNAPSHOT_FORMAT
                or meta["version"] != running_version()
                or meta["fields"] != list(_EVENT_FIELDS)
                or meta["parser"] != _parser_stamp()
                or meta["size"] != len(payload)
                or meta["sha256"] != D.content_digest(payload)
            ):
                return entries
            # Only reached after the file's own sha256, size, ddflow version and parser
            # fingerprint matched (above): local machine state, never merged or fetched.
            self._touch_snapshot()
            order = None
            for name, value in _snapshot_objects(payload):
                if name == "\0order" and isinstance(value, tuple) and len(value) == _ORDER_ARITY:
                    order = value
                elif (frame := _snapshot_frame(name, value)) is not None:
                    entries[name] = frame
            if order is not None:
                names, idxs = order  # a malformed order poisons the whole snapshot
                _SNAP_ORDER[self.dir] = (list(names), list(idxs))
        except Exception:
            entries.clear()
            _SNAP_ORDER.pop(self.dir, None)
        return entries

    def _seed_from_snapshot(self, path: Path, size: int) -> tuple[_Parsed, Any, bytes] | None:
        """A `_Parsed` for this shard built from the snapshot, iff its bytes still match.

        Returns `(parsed, hasher, tail)` with the hasher already holding the prefix (the
        caller continues the digest over the appended tail instead of hashing twice) and
        `tail` the shard's bytes from `consumed` on. Only `[0:consumed]` is read to
        revoke or license the claim and only the tail after it is kept (3.2): the gate
        may reject cheaply, never accept without the bytes -- the hash IS the check --
        and the whole file is never held at once. `None` when the claim does not hold.
        """
        if not self._snapshot_enabled():
            return None
        entry = self._load_snapshot().pop(path.name, None)
        if entry is None:
            return None
        consumed, digest, skipped, tuples = entry
        if not 0 <= consumed <= size:
            return None
        tail, h = _gated_read(path, consumed)
        if h.hexdigest() != digest:
            return None  # the shard is not the one the snapshot describes
        try:
            events = [Event(*t) for t in tuples]
        except Exception:
            return None
        _SNAP_COVERED[self.dir] = _SNAP_COVERED.get(self.dir, 0) + len(events)
        parsed = _Parsed(consumed, digest, events, skipped, object(), True, len(events))
        return parsed, h, tail

    def _maybe_write_snapshot(
        self, deltas: list[tuple[Path, _Delta]], merged: _Merged | None
    ) -> None:
        """Persist the parse when enough has accumulated since the last snapshot.

        Best effort: a snapshot that cannot be written costs only the next cold read.
        Written atomically (temp file + rename) so a concurrent reader sees the old file
        or the new one, never half of either. Streamed (3.1): one shard's tuples are
        marshalled and written at a time, so a read the process will not HOLD past
        `max_cached_events` still persists its parse -- persisting and holding are
        different decisions, and persisting never doubles the payload in memory.
        """
        if not self.cache_writes or not self._snapshot_enabled():
            return
        total = sum(len(d.whole) for _, d in deltas)
        covered = _SNAP_COVERED.get(self.dir, 0)
        if total < self.log_cfg.snapshot_min_events or total - covered < max(1000, covered // 10):
            return
        order = self._saved_order(deltas, merged)
        try:
            target = self._snapshot_path()
            fsio.ensure_ignored_dir(target.parent)
            sized = D.hasher()
            bytes_out = 0
            with fsio.atomic_file(target, fsync=False) as fh:
                fh.write(b" " * (_SNAPSHOT_SLOT - 1) + b"\n")
                cap = self.log_cfg.snapshot_max_mb * 1_000_000
                for frame in self._frames(deltas, order):
                    fh.write(frame)
                    sized.update(frame)
                    bytes_out += len(frame)
                    if (
                        bytes_out + _SNAPSHOT_SLOT > cap
                    ):  # the header slot counts toward the file size
                        raise ValueError("the snapshot is over [log].snapshot_max_mb")
                meta = {
                    "format": SNAPSHOT_FORMAT,
                    "version": running_version(),
                    "fields": list(_EVENT_FIELDS),
                    "parser": _parser_stamp(),
                    "size": bytes_out,
                    "sha256": sized.hexdigest(),
                }
                head = json.dumps(meta).encode()
                if len(head) > _SNAPSHOT_SLOT - 2:
                    raise ValueError("the snapshot header does not fit its reserved slot")
                fh.seek(0)
                fh.write(head + b" " * (_SNAPSHOT_SLOT - 1 - len(head)) + b"\n")
            _SNAP_COVERED[self.dir] = total
            self._collect_stale_snapshots(target)
        except (OSError, ValueError):
            pass

    @staticmethod
    def _saved_order(deltas: list[tuple[Path, _Delta]], merged: _Merged | None) -> tuple | None:
        """The saved merged order `(names, indices)`, when the merged marks describe
        exactly these shards (the sorted, de-duplicated order is derived data too, and
        sorting 100k events is as costly as unmarshalling them: save it as indices into
        the shards' concatenated events so a cold read can skip the sort). `None` when
        it does not fit: an ill-fitting order is dropped, never believed."""
        if merged is None or {p: n for p, (_, n) in merged.marks.items()} != {
            p: len(d.whole) for p, d in deltas
        }:
            return None
        names = [(p.name, len(d.whole)) for p, d in deltas]
        where = {id(e): i for i, e in enumerate(e for _, d in deltas for e in d.whole)}
        return names, [where[id(e)] for e in merged.uniq]

    @staticmethod
    def _frames(deltas: list[tuple[Path, _Delta]], order: tuple | None) -> Iterator[bytes]:
        """One marshalled `(shard name, entry)` frame per shard whose parse may be
        persisted -- held (`d.parsed`) or not (`d.stamp`, 3.1) -- streamed a shard at a
        time so writing never holds a second copy of the log, then the saved order."""
        for p, d in deltas:
            if d.parsed is None and d.stamp is None:
                continue
            consumed, digest, skipped = (
                (d.parsed.consumed, d.parsed.digest, d.parsed.skipped)
                if d.parsed is not None
                else d.stamp
            )
            yield marshal.dumps(
                (p.name, (consumed, digest, skipped, [_event_tuple(e) for e in d.whole]))
            )
        if order is not None:
            yield marshal.dumps(("\0order", order))

    def read_all(self, *, snapshot: bool = True) -> list[Event]:
        """Every event, Lamport-sorted and de-duplicated.

        `snapshot=False` re-derives everything from the log's bytes (what `verify` uses):
        the on-disk snapshot is neither read nor trusted for that call.
        """
        self.skipped_lines = 0
        self._discard_disabled_snapshots()
        live = self.shards()
        # A deleted shard's entry would otherwise live for the process lifetime: nothing
        # visits a path `shards()` no longer returns, so `_read_delta` never sees it. That
        # is a slow leak, and it is also a trap -- `rm -rf .ddflow && ddflow init` under a
        # running server can land a NEW shard on the SAME path, and the digest check would
        # then be the only thing standing between it and the old events.
        if self.log_cfg.reuse_parsed:
            for gone in [p for p in _PARSE_CACHE if p.parent == self.dir and p not in live]:
                del _PARSE_CACHE[gone]
        deltas: list[tuple[Path, _Delta]] = []
        for p in live:
            d = self._read_delta(p, snapshot)
            if d is None:
                continue
            deltas.append((p, d))
            self.skipped_lines += d.skipped
        torn = [e for _, d in deltas for e in d.torn]
        if not self.log_cfg.reuse_parsed or any(d.parsed is None for _, d in deltas):
            if self.log_cfg.reuse_parsed:
                # Over the memory ceiling: the maintained order would outlive its parse.
                # (A cache-disabled reader leaves it alone: it is validated against the
                # parse cache by lineage, so another reader's copy cannot go stale.)
                _MERGED.pop(self.dir, None)
                if snapshot:
                    # 3.1: past the ceiling the parse is STREAMED to the snapshot rather
                    # than held -- persisting is decoupled from holding.
                    self._maybe_write_snapshot(deltas, None)
            return _sorted_unique([e for _, d in deltas for e in d.whole] + torn)
        merged = self._merge(deltas)
        if snapshot:
            self._maybe_write_snapshot(deltas, merged)
        if not torn:
            return list(merged.uniq)
        # A torn fragment is transient and never part of the maintained order.
        return _sorted_unique(merged.uniq + torn)

    def _merged_from_snapshot(self, deltas: list[tuple[Path, _Delta]]) -> _Merged | None:
        """The snapshot's saved order, adopted only if it still describes these events.

        Every shard it names must have been seeded from THIS snapshot with exactly the
        count it was saved with; anything else (a shard rewritten, a shard gone) and the
        order is discarded and rebuilt from the events, the old way. Events appended since
        are not in the order: `_merge` folds them in as it would any append.
        """
        saved = _SNAP_ORDER.pop(self.dir, None)
        if saved is None or not self._snapshot_enabled():
            return None
        try:
            names, order = saved
            by_name = {p.name: (p, d) for p, d in deltas}
            base: list[Event] = []
            marks: dict[Path, tuple[object, int]] = {}
            for name, count in names:
                p, d = by_name[name]
                if d.parsed is None or not d.parsed.seeded or d.parsed.snap_n != count:
                    return None
                base.extend(d.whole[:count])
                marks[p] = (d.parsed.gen, count)
            if len(set(order)) != len(order) or (
                order and (min(order) < 0 or max(order) >= len(base))
            ):
                return None
            uniq = [base[i] for i in order]
            seen = {e.id or e.compute_id() for e in uniq}
            # The order is the one datum the shard hashes do not cover, so check it is what
            # a sort + de-dupe would have made: it holds exactly the distinct events of
            # these shards, and it is in key order.
            if seen != {e.id or e.compute_id() for e in base}:
                return None
            keys = [e.sort_key() for e in uniq]
            if any(a >= b for a, b in itertools.pairwise(keys)):
                return None
            return _Merged(marks, uniq, seen)
        except Exception:
            return None

    def _merge(self, deltas: list[tuple[Path, _Delta]]) -> _Merged:
        """Bring this directory's maintained order up to date with `deltas`.

        Incremental only when PROVABLY equivalent to a from-scratch sort and de-dupe:
        every shard already folded must still be the same byte lineage (`gen`), and no
        appended event may repeat an id already held (a duplicate shard copy, which the
        from-scratch pass resolves by sort order). Anything else rebuilds, which is the
        old behaviour exactly.
        """
        m = _MERGED.get(self.dir)
        if m is not None:
            _SNAP_ORDER.pop(self.dir, None)  # superseded: never adopt it later
        if m is None:
            m = self._merged_from_snapshot(deltas)
            if m is not None:
                _MERGED[self.dir] = m
        fresh: list[Event] = []
        ok = m is not None
        if m is not None:
            paths = {p for p, _ in deltas}
            ok = set(m.marks) <= paths
            for p, d in deltas:
                mark = m.marks.get(p)
                if mark is None:
                    fresh.extend(d.whole)
                elif d.parsed is None or mark[0] is not d.parsed.gen or mark[1] > len(d.whole):
                    ok = False
                    break
                else:
                    fresh.extend(d.whole[mark[1] :])
        if ok and m is not None and len(fresh) <= _MAX_INCREMENTAL:
            fresh.sort(key=Event.sort_key)
            add: list[Event] = []
            ids: set[str] = set()
            for e in fresh:
                eid = e.id or e.compute_id()
                if eid in m.seen:
                    ok = False
                    break
                if eid in ids:
                    continue
                ids.add(eid)
                add.append(e)
            if ok:
                uniq = m.uniq
                for e in add:
                    if not uniq or uniq[-1].sort_key() < e.sort_key():
                        uniq.append(e)
                    else:
                        bisect.insort(uniq, e, key=Event.sort_key)
                m.seen |= ids
                m.marks = {p: (d.parsed.gen, len(d.whole)) for p, d in deltas if d.parsed}
                return m
        whole = [e for _, d in deltas for e in d.whole]
        uniq = _sorted_unique(whole)
        m = _Merged(
            {p: (d.parsed.gen, len(d.whole)) for p, d in deltas if d.parsed},
            uniq,
            {e.id or e.compute_id() for e in uniq},
        )
        _MERGED[self.dir] = m
        return m

    def verify(self) -> list[str]:
        """Integrity check: every event's id must equal the hash of its body."""
        problems = []
        for e in self.read_all(snapshot=False):
            if e.id and e.id != e.compute_id():
                problems.append(
                    f"{e.id}: content does not match its address (edited after the fact?)"
                )
        if self.skipped_lines:
            problems.append(
                f"{self.skipped_lines} unparseable line(s) — likely a torn append after a crash"
            )
        return problems


def _last_line(path: Path) -> str:
    """Read the final non-empty line without reading the file.

    Seeks a small window from the end and grows it. Bounded at 64 KiB: an event larger
    than that is a bug elsewhere, and the bounded read keeps the clock cheap on NFS.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    if size == 0:
        return ""
    window = TAIL_WINDOW_BYTES
    with path.open("rb") as fh:
        while window <= TAIL_MAX_BYTES:
            fh.seek(max(0, size - window))
            chunk = fh.read()
            lines = [ln for ln in chunk.split(b"\n") if ln.strip()]
            if lines and (size <= window or len(lines) > 1):
                return lines[-1].decode("utf-8", "replace")
            window *= 2
    return ""

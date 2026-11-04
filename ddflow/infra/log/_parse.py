"""Event parsing, the incremental read cache, and the on-disk snapshot format.

Owns the process-lifetime caches (``_PARSE_CACHE``, ``_MERGED``, ``_SNAPSHOTS``,
``_SNAP_COVERED``, ``_SNAP_ORDER``); every other submodule imports them from here, so
there is one canonical copy.
"""

from __future__ import annotations

import functools
import io
import marshal
import operator
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from ...core import digest as D
from ...core.events import Event, canonical

#: The on-disk read snapshot (B166), beside the seen marker in the machine-local,
#: git-ignored directory. Never merged and never committed: it is a CACHE of the log,
#: and the log stays the only source of truth.
SNAPSHOT_FILE = "read-snapshot.bin"
# How long a snapshot of OTHER code is kept (`[log].snapshot_stale_days`, one snapshot per
# fingerprint, D-compat) and how often one in use is touched (`[log].snapshot_touch_s`)
# are knobs; so is the size below which none is written (`[log].snapshot_min_events`).
SNAPSHOT_FORMAT = 1
#: A snapshot stores each event as a tuple in `Event`'s own field order, derived from the
#: dataclass so the writer, the reader and the header cannot disagree about the layout.
_EVENT_FIELDS = tuple(f.name for f in fields(Event))
_event_tuple = operator.attrgetter(*_EVENT_FIELDS)
#: `DDFLOW_SNAPSHOT=0` turns the snapshot off (neither read nor written) for a process, and
#: wins over `[log].snapshot`.
SNAPSHOT_ENV = "DDFLOW_SNAPSHOT"


@dataclass(slots=True)
class _Parsed:
    """What has already been parsed out of one shard, and how far into it we got.

    `consumed` is a byte offset just past the last NEWLINE taken, never merely the
    file size: a shard can end in a torn fragment from an append that died mid-write,
    and marking that fragment consumed would skip the event permanently once the
    writer completed it.

    `digest` is a hash of those `consumed` bytes, and it is re-verified on every read.
    That is the whole validity check, and it is a CONTENT check on purpose. The first
    version of this used `st_ino` on the reasoning that "a `git merge` writes a temp
    file and renames, so the inode changes" — which is false, and measurably so:

        $ git checkout -q other && stat -c %i .ddflow/events/a1.jsonl
        218500670
        $ git checkout -q main && stat -c %i .ddflow/events/a1.jsonl
        218500670

    Both `git checkout` and `git merge` rewrite the file IN PLACE. So switching branches
    left a warm cache serving the events of the branch you left, the tail read starting
    mid-line in the new file, and `ddflow doctor` reporting a torn append about an intact
    log — and because `Store.rebuild` takes its fingerprint from `head()` (the real file)
    while taking its events from `read_all()` (the stale set), that wrong state was
    written into the SQLite index and stamped as current. A transient cache bug became
    on-disk corruption that a fresh process would not rebuild away.

    Hashing is affordable precisely because parsing is not: at 20,000 events the prefix
    is 3.58 MB, which reads in 1.1 ms and digests in 4.3 ms, against the 97 ms of
    `Event.from_json` it avoids. A heuristic would save ~5 ms and cost soundness.
    """

    consumed: int
    digest: str
    #: Extended IN PLACE as the shard grows (never copied per read), so a caller must
    #: not hand this list out: `_read_shard` and `read_all` return copies.
    events: list[Event]
    skipped: int
    #: Identity of one unbroken lineage of this shard's bytes. Minted on a full parse and
    #: kept across every extension (which is only taken after the prefix digest verified),
    #: so "same `gen`" means "the events up to any earlier count are still the same
    #: events". `read_all`'s merged order keys on it.
    gen: object = None
    #: True when the events came (in whole or in part) from the on-disk snapshot rather
    #: than from parsing this process's own read of the bytes. `verify()` refuses those.
    seeded: bool = False
    #: How many leading events came from the snapshot (the rest were parsed here).
    snap_n: int = 0


@dataclass(slots=True)
class _Delta:
    """One shard read, in the form `read_all` can merge incrementally."""

    parsed: _Parsed | None  # the cache entry holding `whole`; None when not cached
    whole: list[Event]  # every whole-line event of the shard (the entry's own list)
    torn: list[Event]  # events parsed from an unterminated trailing fragment
    skipped: int  # unparseable lines, torn fragment included
    #: `(consumed, digest, skipped-without-torn)` for the snapshot entry this shard
    #: amounts to, even when it was NOT held (`parsed is None`): the writer may still
    #: stream it (3.1). None when nothing may be persisted from this shard.
    stamp: tuple[int, str, int] | None = None


@dataclass(slots=True)
class _Merged:
    """The de-duplicated, Lamport-sorted union of one events directory (B169).

    `read_all` used to sort every event and re-hash every id on EVERY call, which kept the
    warm read linear in the log even though the parse was already incremental. This holds
    the result and folds only what was appended since into it.
    """

    marks: dict[Path, tuple[object, int]]  # shard -> (lineage, whole events accounted for)
    uniq: list[Event]
    seen: set[str]


#: Absolute shard path -> what has been parsed from it. Process-lifetime and keyed by
#: path rather than held on the instance, because a fresh `EventLog` is built per API
#: call and per MCP tool call — an instance-level cache would never be hit twice.
#:
#: Sound ONLY because the log is append-only, and for a reason worth stating: an
#: incremental *projector* can disagree with `fold` and so is refused outright
#: (`Store.rebuild`), but an incremental *reader* cannot, because each line parses
#: independently of every other. Re-using a parse is not a second implementation of
#: anything.
#:
#: **Unlocked, because nothing in this process is concurrent.** `mcp.serve` is a strictly
#: sequential `for raw in stdin:` loop — no threads, no asyncio — so a request is finished
#: before the next is read. Adding concurrency to that loop means guarding this dict.
#:
#: No eviction policy beyond the ceiling: once full, the shards already in it keep their
#: entries and later ones are simply not cached. That is unfair rather than wrong, and a
#: project with enough shards to notice has a bigger problem (B166).
_PARSE_CACHE: dict[Path, _Parsed] = {}

#: Events directory -> its merged, sorted, de-duplicated order. Derived entirely from
#: `_PARSE_CACHE` (and re-validated against it on every read), so it can be dropped at any
#: moment at the cost of one full sort.
_MERGED: dict[Path, _Merged] = {}

#: Events directory -> snapshot entries not yet consumed by a shard read in this process
#: (shard file name -> (consumed, digest, skipped, event tuples)). Presence of the key
#: means "already tried to load"; the dict is emptied as shards are seeded.
_SNAPSHOTS: dict[Path, dict[str, tuple]] = {}

#: Events directory -> how many events the on-disk snapshot is known to cover, to decide
#: when the tail since then is worth a rewrite.
_SNAP_COVERED: dict[Path, int] = {}
#: logs whose disabled-snapshot files were already collected in this process
_SNAP_SWEPT: set[Path] = set()
_SNAP_SWEPT_MAX = 256

#: Events directory -> the snapshot's saved merged order, for `_merge` to adopt once:
#: ([(shard name, events snapshotted)], flat indices into the concatenation of those
#: shards' snapshotted events, in sorted de-duplicated order).
_SNAP_ORDER: dict[Path, tuple[list[tuple[str, int]], list[int]]] = {}

#: Appends no larger than this are placed by bisection; a bigger batch rebuilds.
_MAX_INCREMENTAL = 64

#: Bytes one hasher call drains from a shard at a time: the gate window is streamed,
#: never materialised (3.2).
_READ_CHUNK = 1 << 20

#: Arity of one snapshot entry frame: (consumed, digest, skipped, event tuples).
_SNAPSHOT_ARITY = 4

#: Arity of the saved order frame: (shard names, indices into their events).
_ORDER_ARITY = 2

#: Leading bytes a streamed snapshot reserves for its header: the header's size and
#: sha256 settle only after the payload has streamed, so it occupies a fixed slot it
#: must fit inside. A header that does not fit aborts the write (it is best effort).
_SNAPSHOT_SLOT = 512


def clear_parse_cache() -> None:
    """Drop every parsed shard.

    For tests. NOT needed after a merge or a branch switch — the per-read digest check
    detects those, which is the point of making it a content check. Nothing in `ddflow`
    calls this, and that is the correct amount.
    """
    _PARSE_CACHE.clear()
    _MERGED.clear()
    _SNAPSHOTS.clear()
    _SNAP_COVERED.clear()
    _SNAP_SWEPT.clear()
    _SNAP_ORDER.clear()


def _cached_events() -> int:
    """How many events the whole cache is holding, across every shard of every repo."""
    return sum(len(e.events) for e in _PARSE_CACHE.values())


def _digest(data: bytes) -> str:
    """Are these the same bytes? An ephemeral, in-memory comparison — NOT a content
    address.

    Deliberately not the repo's `blake2b` convention (`events.Event.compute_id`,
    `ids.short_id`), and the distinction matters: those are stable identities that are
    written to disk and cannot change without invalidating every stored id. This one is
    never persisted and never compared across processes, so the only criterion is speed
    over a multi-megabyte buffer — where `sha256` wins on CPU acceleration, measured at
    4.3 ms per 3.58 MB against blake2b's 10.4 ms.
    """
    return D.content_digest(data)


def _gated_read(path: Path, upto: int) -> tuple[bytes, Any]:
    """Bytes `[upto:]` plus a hasher streamed over `[0:upto]`, from ONE open (3.2).

    The gate window is the licence for skipping the parse of those bytes, so gate and
    tail must come from the same read. Nothing before `upto` is materialised and nothing
    after it is hashed: the held copy of a shard is only the tail the caller will parse.
    A file that turns out shorter than `upto` returns `b""` with an empty hasher — a
    mismatch the caller reads as "reparse it all" (the gate may only reject, never
    accept). `upto == 0` returns the whole file with that empty hasher. Only
    `FileNotFoundError` can surface, and the caller decides it is the benign one.
    """
    h = D.hasher()
    want = upto
    with path.open("rb") as fh:
        while want:
            block = fh.read(min(want, _READ_CHUNK))
            if not block:
                return b"", D.hasher()
            h.update(block)
            want -= len(block)
        return fh.read(), h


def _license_cached(path: Path, cached: _Parsed, size: int) -> tuple[bytes, Any] | None:
    """`(tail, hasher holding [0:consumed])` when `cached`'s claim still describes the
    bytes -- the ONLY licence for re-using a parse is the content hash (3.2): a shorter
    file rejects before any read, anything else is re-checked by the digest. `None`
    means the parse must start from 0 (`_gated_read` of the whole file)."""
    if cached.consumed > size:
        return None
    chunk, h = _gated_read(path, cached.consumed)
    if h.hexdigest() != cached.digest:
        return None
    return chunk, h


def _license(
    path: Path, cached: _Parsed | None, size: int
) -> tuple[bytes, Any, int, int, _Parsed | None]:
    """`(chunk, hasher, start, skipped, base)` licensing everything short of a whole-file
    parse: a fresh shard (no claim) yields the whole file; a cached claim that still
    matches its digest yields only the bytes after its `consumed` mark, with the hasher
    already holding the prefix, and that cached parse as the base the freshly parsed tail
    extends (3.2). An unlicensed claim yields the whole file with NO base: the bytes win
    every argument, and a parse that starts from 0 must own its events outright."""
    if cached is None:
        chunk, _ = _gated_read(path, 0)
        return chunk, None, 0, 0, None
    licensed = _license_cached(path, cached, size)
    if licensed is None:
        chunk, _ = _gated_read(path, 0)
        return chunk, None, 0, 0, None
    chunk, hasher = licensed
    return chunk, hasher, cached.consumed, cached.skipped, cached


def _snapshot_frame(name: Any, value: Any) -> tuple | None:
    """The per-shard entry `(consumed, digest, skipped, tuples)` of a decoded snapshot
    frame, or `None` when the frame is not one (a malformed frame is a doubt, and a
    doubted snapshot degrades to a slower correct read)."""
    if not (isinstance(name, str) and isinstance(value, tuple) and len(value) == _SNAPSHOT_ARITY):
        return None
    consumed, digest, skipped, tuples = value
    if isinstance(consumed, int) and isinstance(digest, str) and isinstance(skipped, int):
        return value if isinstance(tuples, list) else None
    return None


def _snapshot_objects(payload: bytes) -> list[tuple]:
    """The decoded `(name, value)` pairs of a snapshot payload: the streamed layout is
    one marshalled frame per pair, and the pre-3.1 one-object dict layout is its items --
    either way the caller sees pairs, and a frame that is neither yields nothing."""
    bio = io.BytesIO(payload)
    first = marshal.load(bio)  # nosec B302
    if isinstance(first, dict):
        return list(first.items())
    objects = [first]
    while bio.tell() < len(payload):
        objects.append(marshal.load(bio))  # nosec B302
    return objects


@functools.lru_cache(maxsize=1)
def _parser_stamp() -> str:
    """A fingerprint of the code that turns a line into an Event.

    A snapshot holds PARSED events, so it must die when the parser changes even if the
    version string does not (a development checkout, a patched install): hashing the
    bytecode and constants of `Event.from_json` and `Event.compute_id` is cheap and needs
    no list of "things that affect parsing" to keep current.
    """
    h = D.hasher()
    for fn in (
        Event.from_json,
        Event.compute_id,
        Event.body,
        canonical,
        _parse_lines,
        _parse_event,
        _recover_glued,
    ):
        code = fn.__code__
        h.update(code.co_code)
        h.update(repr(code.co_consts).encode())
        h.update(repr(code.co_names).encode())
    return h.hexdigest()[:16]


#: Every line `Event.to_json` writes starts with this (`canonical` sorts the keys).
_LINE_START = '{"agent":'


def _parse_event(line: str) -> Event | None:
    """One line as an Event, or None. Any JSON that is not an event object is None, not
    an exception: a torn fragment can be any prefix of a line, including `123`."""
    try:
        return Event.from_json(line)
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def _recover_glued(line: str) -> Event | None:
    """The whole event an older writer appended straight onto a torn fragment (B28b3839fe6).

    Before the writer terminated a torn tail, the next append landed on the fragment's line
    as `<fragment><event>`, and the event was lost with it. Each later start of an event
    line is tried as the event; one is accepted only when its id is the hash of its body,
    so a fragment cannot be resurrected as something it never was.
    """
    at = line.find(_LINE_START, 1)
    while at > 0:
        ev = _parse_event(line[at:])
        if ev is not None and ev.id and ev.id == ev.compute_id():
            return ev
        at = line.find(_LINE_START, at + 1)
    return None


def _parse_lines(chunk: bytes) -> tuple[list[Event], int]:
    """`(events, unparseable-line-count)` for a run of whole lines."""
    out: list[Event] = []
    skipped = 0
    # "\n" alone ends a line: `splitlines()` also breaks on U+2028, U+2029 and U+0085, which
    # `canonical` writes raw inside a string, and cut such an event in two (B5035a55092).
    for raw in chunk.decode("utf-8", errors="replace").split("\n"):
        line = raw.strip()
        if not line:
            continue
        ev = _parse_event(line)
        if ev is None:
            skipped += 1  # the fragment is still reported, recovered event or not
            ev = _recover_glued(line)
        if ev is not None:
            out.append(ev)
    return out, skipped


def _sorted_unique(events: list[Event]) -> list[Event]:
    """Lamport-sorted and de-duplicated by content address -- the reference definition.

    Merging two branches can bring the same event in twice via two shard copies, and a
    union must be idempotent.
    """
    seen: set[str] = set()
    uniq: list[Event] = []
    for e in sorted(events, key=Event.sort_key):
        eid = e.id or e.compute_id()
        if eid in seen:
            continue
        seen.add(eid)
        uniq.append(e)
    return uniq

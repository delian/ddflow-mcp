"""What distinguishes one index from another: the code fingerprint and the SQLite
capabilities the projection is built on.

The fingerprint names the index file, and is why two checkouts running different code
each keep their own index instead of rebuilding each other's. The capability probes
(`_has_fts5`, `_has_trigram`) and the ranked-column map (`_SEARCH_COLS`) are shared by
the schema, projection and search halves of the store.
"""

from __future__ import annotations

import contextlib
import errno
import functools
import math
import os
import re
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from ddflow import FORMAT_LEVEL, __version__

from ...core import digest as D
from ...core import textsim

SCHEMA = 11
#: Bump when the FOLD (how events become state) or the projection changes what an index
#: holds without a SCHEMA change. The index is keyed on this, not on a hash of the source,
#: so a developer edit or a patch upgrade does not cold-rebuild (bug Bece55b8a77).
#: `DDFLOW_INDEX_KEY=source` keys on the source hash again (the pre-0.2.11 behaviour).
FOLD_REV = 1

#: At most this many index files are kept (this one included); `DDFLOW_INDEX_MAX_FILES`.
INDEX_MAX_FILES = 3
#: And at most this many MB of index files in total; `DDFLOW_INDEX_MAX_MB`. 0 = no size cap.
INDEX_MAX_MB = 1024.0

#: An index in use is touched at most this often, so its mtime means "last used", not "last
#: rebuilt", and `[store].stale_index_days` of silence really is disuse.
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
    pkg = sys.modules[__package__]
    if os.environ.get("DDFLOW_INDEX_KEY", "").strip().lower() != "source":
        # Explicit key (r-derived-cache-bounded): what the index holds, not what built it.
        return D.hasher(
            f"{pkg.SCHEMA}/{pkg.FOLD_REV}/{textsim.VERSION}/{FORMAT_LEVEL}".encode()
        ).hexdigest()[:16]
    h = D.hasher(f"{pkg.SCHEMA}/{textsim.VERSION}/{FORMAT_LEVEL}".encode())
    here = Path(__file__).resolve()
    # This module is one level deeper than the pre-package `store.py` was, so the package
    # root (`ddflow/`) is `here`'s great-grandparent; "this module" is now the whole
    # `ddflow/infra/store/` package.
    root = here.parent.parent.parent
    core = root / "core"
    # The store package files are always present in an install (they are the module); it is
    # the CORE source tree whose absence means "no source to read", so the fallback counts
    # core files only — counting the seven package files too would make the check always
    # false in a zipped/bytecode-only install and the release could no longer stand in.
    core_sources = sorted(core.rglob("*.py"))
    sources = sorted([*core_sources, *here.parent.rglob("*.py")])
    for f in sources:
        h.update(f.relative_to(root).as_posix().encode())
        with contextlib.suppress(OSError):
            h.update(f.read_bytes())
    if len(core_sources) < MIN_FINGERPRINT_SOURCES:
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


def _capability_tables(con, fts: bool, trig: bool) -> bool:
    """Are the derived tables the capability needs actually present AND in sync with their
    base tables, whatever `meta` claims? The claim can say `trigram=1` while the `_tri`
    tables never grew (the reconcile `_rebuild_locked` refuses stamped the claim over an
    index built without them) or grew empty (a read's `init` created them, unfilled), so
    the capability is verified from the tables: each derived table holds one row per base
    row, the same count the reconcile itself treats as the honest "in sync"."""
    if not (fts or trig):
        return True
    try:
        names = {r[0] for r in con.execute("select name from sqlite_master where type = 'table'")}
        for table in _SEARCH_COLS:  # nosec B608: fixed keys
            # bandit B608: `table` is a key of the fixed `_SEARCH_COLS`.
            base = con.execute(f"select count(*) from {table}").fetchone()[0]  # nosec B608
            if fts:
                name = f"{table}_fts"
                if name not in names or (
                    con.execute(f"select count(*) from {name}").fetchone()[0] != base  # nosec B608
                ):
                    return False
            if trig:
                name = f"{table}_tri"
                if name not in names or (
                    con.execute(f"select count(*) from {name}").fetchone()[0] != base  # nosec B608
                ):
                    return False
    except sqlite3.DatabaseError:
        return False
    return True


_COMPANIONS = ("-wal", "-shm", "-lock")


def _env_num(name: str, default: float) -> float:
    """A positive finite number from the environment, else `default` (nan, inf and
    garbage never reach the caps)."""
    try:
        v = float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default
    return v if math.isfinite(v) and v >= 0 else default


def collect_over_cap(directory: Path, keep: str) -> list[Path]:
    """Delete the oldest index files (with their -wal/-shm/-lock companions) beyond the
    count and size caps, never `keep` (the index in use). Best effort; an index is derived,
    so a mistaken removal costs one rebuild. `DDFLOW_INDEX_GC=0` turns it off, and
    `DDFLOW_INDEX_MAX_FILES` / `DDFLOW_INDEX_MAX_MB` set the caps (the size of a file
    counts its companions; companions whose main file is gone are removed)."""
    if os.environ.get("DDFLOW_INDEX_GC", "").strip().lower() in ("0", "false", "off", "no"):
        return []
    max_files = max(1, int(_env_num("DDFLOW_INDEX_MAX_FILES", INDEX_MAX_FILES)))
    max_bytes = _env_num("DDFLOW_INDEX_MAX_MB", INDEX_MAX_MB) * 1048576

    def sized(f: Path) -> int:
        n = 0
        for suf in ("", *_COMPANIONS):
            with contextlib.suppress(OSError):
                n += f.with_name(f.name + suf).stat().st_size
        return n

    def remove(f: Path) -> None:
        for suf in ("", *_COMPANIONS):
            with contextlib.suppress(OSError):
                f.with_name(f.name + suf).unlink(missing_ok=True)

    names = {p.name for p in directory.glob("index.db*")}
    mains: list[tuple[float, int, Path]] = []
    for f in map(directory.joinpath, sorted(names)):
        m = _INDEX_FILE.match(f.name)
        if not m or f.name == keep:
            continue
        if m.group(2) in _COMPANIONS:  # an orphan companion: its main file is gone
            if f.name[: -len(m.group(2))] not in names:
                with contextlib.suppress(OSError):
                    f.unlink()
            continue
        if m.group(2):
            continue  # a rebuild's temp file: aged out by the rebuild itself
        with contextlib.suppress(OSError):
            mains.append((f.stat().st_mtime, sized(f), f))
    mains.sort(key=lambda t: t[0], reverse=True)  # newest first
    total = sized(directory / keep)
    kept = 1
    gone: list[Path] = []
    full = False  # once one file is over a cap, every older one goes too (oldest first)
    for _, size, f in mains:
        if not full and kept < max_files and (max_bytes <= 0 or total + size <= max_bytes):
            kept += 1
            total += size
        else:
            full = True
            remove(f)
            gone.append(f)
    return gone

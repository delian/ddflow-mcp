"""LocalStore: the one place machine-local state is read, written, locked and trimmed
(D-unify, B-uni-local-worker.3-store).

Machine-local state (`.ddflow/local`: caches, queues, signal rings, reports) grew a store
per feature, each with its own lock, its own half-atomic write, no format field and its
own retention. This module is the primitive they move onto, slice by slice; nothing calls it
yet. It gives:

* a **document** -- one JSON value in one file, replaced atomically (`fsio.atomic_write`),
  stamped with a schema number and the writer's version, read-modify-written under a lock;
* a **ring** -- the last N records, persisted, so a signal sampler cannot grow a file;
* a **queue** -- persisted, coalescing work: a second `put` of a key already waiting
  replaces its payload instead of queueing twice, and a key becomes due only after it has
  been quiet for `debounce_s`; a crash leaves the old queue or the new one, never a torn one;
* **Retention** -- keep the newest N files, drop those older than an age, trim to a size.

Compatibility (D-compat 2): a document written by a NEWER schema is refused for what would
lose its content (`NewerContent`, exit 3, "upgrade ddflow to >= X"); keys this version does
not know are kept when it rewrites a document of its own schema. Unreadable state is never
silently an empty store: `read` raises `StoreUnreadable` (its `default` is for a MISSING
document only), because a quota or lease that "loses" its file grants itself everything.

Every time comes from the injected `clock` (default `time.time`), so debounce and retention
are testable without sleeping. The directory ignores itself (`fsio.ensure_ignored_dir`).
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ddflow import FORMAT_LEVEL, __version__

from . import fsio

#: Seconds `LocalStore.lock` waits for another holder by default; a stuck holder must not
#: hang a hook that is only maintaining a cache.
LOCK_TIMEOUT_S = 10.0

#: The envelope's own keys; everything else at the top level is another version's, kept.
_ENVELOPE = ("schema", "ddflow", "fmt", "data")

Clock = Callable[[], float]


class StoreUnreadable(RuntimeError):
    """A store file exists but is not a document this code can read (torn by another tool,
    not JSON, not an object). Carries the path; the caller words its own refusal."""

    def __init__(self, path: Path, why: str) -> None:
        super().__init__(f"{path} cannot be read: {why}")
        self.path = path
        self.why = why


@dataclass(frozen=True, slots=True)
class Doc:
    """A document as read: its value, the schema it was written under, and the keys of the
    envelope this version does not know (`extra`, preserved by `LocalStore.update`)."""

    data: Any
    schema: int
    extra: tuple[tuple[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class Retention:
    """What to keep of a directory of files: at most `keep_n` newest, none older than
    `max_age_s`, and at most `max_bytes` in total (oldest dropped first). `None` is no limit.

    `sweep` removes by modification time and reports what it removed. Dotfiles (a directory's
    own `.gitignore`, a lock) are never touched."""

    keep_n: int | None = None
    max_age_s: float | None = None
    max_bytes: int | None = None

    def sweep(self, directory: Path | str, *, now: float | None = None) -> list[Path]:
        d = Path(directory)
        at = time.time() if now is None else now
        files: list[tuple[float, int, Path]] = []
        with contextlib.suppress(FileNotFoundError):
            for p in d.iterdir():
                if p.name.startswith(".") or not p.is_file():
                    continue
                with contextlib.suppress(OSError):
                    st = p.stat()
                    files.append((st.st_mtime, st.st_size, p))
        files.sort(key=lambda f: (-f[0], f[2].name))  # newest first, ties by name
        doomed: list[Path] = []
        kept: list[tuple[float, int, Path]] = []
        for i, f in enumerate(files):
            too_many = self.keep_n is not None and i >= self.keep_n
            too_old = self.max_age_s is not None and at - f[0] > self.max_age_s
            if too_many or too_old:
                doomed.append(f[2])
            else:
                kept.append(f)
        if self.max_bytes is not None:
            total = sum(f[1] for f in kept)
            while kept and total > self.max_bytes:
                _mtime, size, victim = kept.pop()  # the oldest of what is left
                total -= size
                doomed.append(victim)
        removed = []
        for p in doomed:
            try:
                p.unlink()
            except OSError:
                continue  # swept by someone else (gone is the goal), or not ours to remove
            removed.append(p)
        return removed


class LocalStore:
    """The machine-local directory `root` (created, and ignoring itself, on first write)."""

    def __init__(self, root: Path | str, *, clock: Clock = time.time) -> None:
        self.root = Path(root)
        self.clock = clock

    # -- paths and locks ---------------------------------------------------------------

    def path(self, name: str) -> Path:
        """The file of the store `name` (a plain file name: no separators)."""
        if not name or name != Path(name).name or name.startswith("."):
            raise ValueError(f"{name!r} is not a plain store name")
        return self.root / name

    @contextlib.contextmanager
    def lock(self, name: str, timeout_s: float | None = LOCK_TIMEOUT_S) -> Iterator[None]:
        """Exclusive lock on the store `name`; `fsio.LockTimeout` after `timeout_s`."""
        self._ensure()
        with fsio.file_lock(fsio.lock_path_for(self.path(name)), timeout_s):
            yield

    def _ensure(self) -> None:
        fsio.ensure_ignored_dir(self.root, comment="ddflow machine-local state: never committed")

    # -- documents ---------------------------------------------------------------------

    def read(self, name: str, *, schema: int = 1, default: Any = None) -> Any:
        """The value of the document `name`. Missing: `default` (only then). A document of a NEWER
        schema than `schema` raises `fsio.NewerContent` (it holds what this code cannot
        interpret); one that cannot be read raises `StoreUnreadable`."""
        doc = self._load(name, schema)
        return default if doc is None else doc.data

    def write(self, name: str, data: Any, *, schema: int = 1, mode: int | None = None) -> None:
        """Replace the document `name` with `data` (atomically, under its lock). Refuses to
        replace a document of a newer schema; keeps the envelope keys it does not know."""
        with self.lock(name):
            self._store(name, data, schema, mode)

    def update(
        self,
        name: str,
        fn: Callable[[Any], Any],
        *,
        schema: int = 1,
        default: Any = None,
        mode: int | None = None,
    ) -> Any:
        """Read-modify-write under the lock: `fn(current or default)` is the new value,
        which is returned. Two agents updating at once both land."""
        with self.lock(name):
            doc = self._load(name, schema)
            new = fn(default if doc is None else doc.data)
            self._store(name, new, schema, mode, doc)
            return new

    def _load(self, name: str, schema: int) -> Doc | None:
        path = self.path(name)
        try:
            text = path.read_text("utf-8")
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError) as exc:
            raise StoreUnreadable(path, str(exc)) from exc
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise StoreUnreadable(path, f"not JSON ({exc})") from exc
        if not isinstance(raw, dict):
            raise StoreUnreadable(path, "not a JSON object")
        # A file with no envelope predates the store: schema 0, its whole value is the data.
        if "schema" not in raw:
            return Doc(raw, 0)
        found = raw["schema"]
        if not isinstance(found, int) or isinstance(found, bool):
            raise StoreUnreadable(path, f"schema {found!r} is not a number")
        if found > schema:
            fmt = raw.get("fmt")
            raise fsio.NewerContent(
                str(path),
                str(raw.get("ddflow", "a newer version")),
                fmt if isinstance(fmt, int) and not isinstance(fmt, bool) else found,
            )
        extra = tuple((k, v) for k, v in raw.items() if k not in _ENVELOPE)
        return Doc(raw.get("data"), found, extra)

    def _store(
        self, name: str, data: Any, schema: int, mode: int | None, known: Doc | None = None
    ) -> None:
        doc = known if known is not None else self._load(name, schema)  # raises on newer
        body: dict[str, Any] = {
            "schema": schema,
            "ddflow": __version__,
            "fmt": FORMAT_LEVEL,
            "data": data,
        }
        if doc is not None:
            body.update(dict(doc.extra))
        fsio.atomic_write(
            self.path(name), json.dumps(body, indent=1, sort_keys=True) + "\n", mode=mode
        )

    # -- rings -------------------------------------------------------------------------

    def ring_append(self, name: str, item: Any, *, capacity: int, schema: int = 1) -> list[Any]:
        """Append `item` to the ring `name`, keeping only the last `capacity` records.
        Returns the ring after the append."""
        if capacity < 1:
            raise ValueError("a ring holds at least one record")
        out = self.update(
            name,
            lambda cur: [*_as_list(cur), item][-capacity:],
            schema=schema,
            default=[],
        )
        return list(out)

    def ring(self, name: str, *, schema: int = 1) -> list[Any]:
        """The records of the ring `name`, oldest first."""
        return list(_as_list(self.read(name, schema=schema, default=[])))

    # -- the coalescing queue ----------------------------------------------------------

    def queue_put(
        self, name: str, key: str, payload: Any, *, debounce_s: float = 0.0, schema: int = 1
    ) -> int:
        """Queue `payload` under `key`. A key already waiting is COALESCED: its payload is
        replaced, its `first_at` kept, its `count` raised and its quiet period restarted.
        Returns how many puts this key now stands for."""

        def put(cur: Any) -> dict[str, Any]:
            now = self.clock()  # read under the lock, so last_at never runs backwards
            q = dict(cur) if isinstance(cur, dict) else {}
            old = q.get(key)
            q[key] = {
                "payload": payload,
                "first_at": old["first_at"] if isinstance(old, dict) else now,
                "last_at": now,
                "count": (old["count"] if isinstance(old, dict) else 0) + 1,
                "debounce_s": debounce_s,
            }
            return q

        return int(self.update(name, put, schema=schema, default={})[key]["count"])

    def queue_pending(self, name: str, *, schema: int = 1) -> dict[str, dict[str, Any]]:
        """Everything waiting, due or not, oldest first."""
        q = self.read(name, schema=schema, default={})
        items = q.items() if isinstance(q, dict) else ()
        return dict(sorted(items, key=lambda kv: (kv[1].get("first_at", 0), kv[0])))

    def queue_take(
        self, name: str, *, limit: int | None = None, schema: int = 1
    ) -> list[tuple[str, dict[str, Any]]]:
        """Remove and return the entries that are due (quiet for their `debounce_s`), oldest
        first, at most `limit`. Taking is one atomic swap: a crash before it leaves the
        entries queued, after it leaves them taken, never half."""
        taken: list[tuple[str, dict[str, Any]]] = []

        def take(cur: Any) -> dict[str, Any]:
            now = self.clock()
            q = dict(cur) if isinstance(cur, dict) else {}
            order = sorted(q.items(), key=lambda kv: (kv[1].get("first_at", 0), kv[0]))
            for key, entry in order:
                if limit is not None and len(taken) >= limit:
                    break
                if now - entry.get("last_at", 0) >= entry.get("debounce_s", 0.0):
                    taken.append((key, entry))
                    del q[key]
            return q

        self.update(name, take, schema=schema, default={})
        return taken

    # -- housekeeping ------------------------------------------------------------------

    def sweep(self, subdir: str, retention: Retention) -> list[Path]:
        """Apply `retention` to the files of `subdir` under the root (a plain directory name:
        a retention sweep deletes, so it never leaves the store)."""
        return retention.sweep(self.path(subdir), now=self.clock())

    def remove(self, name: str) -> None:
        """Delete the document `name`, under its lock; a missing one is fine. The lock file
        stays: unlinking it would let a second holder lock a new inode beside the first."""
        with self.lock(name), contextlib.suppress(FileNotFoundError):
            os.unlink(self.path(name))


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []

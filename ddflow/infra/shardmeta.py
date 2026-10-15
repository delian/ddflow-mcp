"""What an append needs to know about the shards, remembered per clone (B-gap-log-scale).

Every append needs (a) the highest Lamport value of every shard and (b) the stamp events
(`ddflow.seen`, sessions, skew overrides, capabilities) the version guard folds -- and both
meant parsing every shard in a fresh process (0.5 s at 63k events). This keeps, per shard,
`{size, mtime_ns, high, stamps}` in `.ddflow/local/shardmeta/<shard>.json` (LocalStore
documents: machine-local, derived, never committed) and trusts a record only while the shard
still has exactly that size and modification time. Any other shard state -- it grew (another
agent appended, a merge delivered events), shrank, was rewritten or is new -- sends THAT shard
back to a full read; no other shard is read. A shard's tail is not its maximum (two clones
under one agent id merge into [1, 2, 3, 4, 2]), so no record is derived from a tail.

A cache: a missing, unreadable or newer-schema document, or any error, is a full read.
Callers hold the log's append lock, which serialises the writers of these documents.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..core.events import Event
from .localstore import LocalStore

LAYOUT = 1
DIR = Path(".ddflow") / "local" / "shardmeta"

#: Kinds the version guard folds (`core.events.stamp_facts`): kept in full per shard.
STAMP_KINDS = frozenset(
    {"ddflow.seen", "session.started", "session.ended", "skew.overridden", "ddflow.capabilities"}
)


class ShardMeta:
    def __init__(self, root: Path) -> None:
        self._store = LocalStore(Path(root) / DIR)
        self._rows: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _stamp(path: Path) -> tuple[int, int] | None:
        try:
            st = path.stat()
        except OSError:
            return None
        return st.st_size, st.st_mtime_ns

    def _doc(self, name: str) -> dict[str, Any] | None:
        row = self._rows.get(name)
        if row is None:
            try:
                row = self._store.read(name + ".json", schema=LAYOUT, default=None, derived=True)
            except Exception:
                row = None
            if not isinstance(row, dict):
                return None
            self._rows[name] = row
        return row

    def _current(self, path: Path) -> dict[str, Any] | None:
        stamp = self._stamp(path)
        row = self._doc(path.name)
        if (
            stamp is None
            or row is None
            or (row.get("size"), row.get("mtime_ns")) != stamp
            or not isinstance(row.get("high"), int)
            or not isinstance(row.get("stamps"), list)
        ):
            return None
        return row

    def facts(
        self, shards: list[Path], read: Callable[[Path], list[Event]]
    ) -> tuple[int, list[Event]]:
        """(highest lamport, every stamp event) over ``shards``; ``read(path)`` reads one
        shard in full, and is called only for a shard with no current record."""
        high, stamps = 0, []
        for p in shards:
            row = self._current(p)
            if row is None:
                stamp = self._stamp(p)
                events = read(p)
                row = {
                    "size": stamp[0] if stamp else -1,
                    "mtime_ns": stamp[1] if stamp else -1,
                    "high": max((e.lamport for e in events), default=0),
                    "stamps": [e.to_json() for e in events if e.kind in STAMP_KINDS],
                }
                if stamp is not None:
                    self._save(p.name, row)
            high = max(high, row["high"])
            stamps.extend(Event.from_json(line) for line in row["stamps"])
        return high, stamps

    def advance(self, shard: Path, before: tuple[int, int], ev: Event) -> None:
        """``ev`` was appended to ``shard`` (lock held) when it had the ``(size, mtime_ns)``
        ``before``. The record is extended only if it described the shard as it was just
        before -- both numbers, so a same-length rewrite is not laundered into a current
        record; any doubt leaves the shard to be re-read."""
        row = self._rows.get(shard.name)
        stamp = self._stamp(shard)
        if row is None or stamp is None or (row.get("size"), row.get("mtime_ns")) != before:
            self._rows.pop(shard.name, None)
            return
        new = {
            "size": stamp[0],
            "mtime_ns": stamp[1],
            "high": max(int(row["high"]), ev.lamport),
            "stamps": [*row["stamps"], *([ev.to_json()] if ev.kind in STAMP_KINDS else [])],
        }
        self._save(shard.name, new)

    def _save(self, name: str, row: dict[str, Any]) -> None:
        self._rows[name] = row
        with contextlib.suppress(Exception):
            self._store.write(name + ".json", row, schema=LAYOUT, derived=True)


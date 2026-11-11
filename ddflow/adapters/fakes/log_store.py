"""The shipped fake of the `LogStore` port: one in-memory sharded log, no disk, no lock
files (the ETA simulator's and the swarm tests' stand-in).

A shard is a per-``agent`` list of events; ordering is a monotonic sequence shared across
all shards. Content digests come from the `Digest` port (the sha256 the blob ref names),
not a second hashing path in this module (r-reuse-core).
"""

from __future__ import annotations

import copy
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from ddflow.adapters import digest as D

from . import fake

#: The blake2b digest length of an event id: the same 12 bytes `core.events` uses, so the
#: fake's content address is the same shape as a real event's.
_ID_SIZE = 12

#: The hex length of a sha256 digest: the local store's blob ref names 64 hex characters.
_HEX_LEN = 64


@dataclass(frozen=True)
class _Event:
    """What the fake records for one event: the fields a caller reads, and the `sort_key`
    the fold sorts by -- the storage surface, none of the fold's domain semantics. `data`
    is the payload snapshotted at append; `append`/`read_from`/`snapshot` hand a FRESH
    deep copy back, so the jsonl store's re-parse-on-read is matched, not mimicked."""

    kind: str
    subject: str
    data: Any
    agent: str
    lamport: int
    id: str

    def sort_key(self) -> tuple[int, str, str]:
        return (self.lamport, self.agent, self.id)


def _eid(kind: str, subject: str, data: dict[str, Any], agent: str, lamport: int) -> str:
    """A content address of ``body`` in the event's shape: the sequence differs per append,
    so two appends of one logical event are two events, as two writes would be."""
    body = {"kind": kind, "subject": subject, "data": data, "agent": agent, "lamport": lamport}
    return "e" + D.of_obj(body, size=_ID_SIZE)


def _address(raw: bytes) -> str:
    """The content digest a blob ref names: ``sha256:<hex>`` of ``raw`` (via the `Digest`
    port's sha256, so the fake computes the same address the local store checks)."""
    return f"sha256:{D.content_digest(raw)}"


def _is_hex_ref(ref: str) -> bool:
    algo, sep, hexed = ref.partition(":")
    return (
        sep == ":"
        and algo == "sha256"
        and len(hexed) == _HEX_LEN
        and all(c in "0123456789abcdef" for c in hexed)
    )


@fake
class FakeLogStore:
    """A `LogStore` over memory: ``_shards`` maps a shard name (`<agent>.jsonl`) to its
    events; ``_blobs`` maps a digest to its bytes. Writes and structure reads all hold one
    `threading.Lock`; `append` fills in the next sequence number. `agent=` on `append` is a
    fake-only extra (the port's one append writes THIS store's shard) so swarm tests can
    record several agents in one fake; `compact` merges every shard into the default agent's
    and returns how many were merged away."""

    def __init__(self, root: Any = None) -> None:
        self._shards: dict[str, list[_Event]] = {}
        self._blobs: dict[str, bytes] = {}
        self._lamport = 0
        self._lock = threading.Lock()
        self._default_agent = "agent"

    def append(self, kind, subject, data=None, *, agent=""):
        with self._lock:
            return copy.deepcopy(self._write(kind, subject, data, agent or self._default_agent))

    def append_many(self, items):
        with self._lock:
            return [
                copy.deepcopy(self._write(kind, subject, data, self._default_agent))
                for kind, subject, data in items
            ]

    def _write(self, kind: str, subject: str, data: Any, agent: str) -> _Event:
        """One event, with the lock already held (an `append` nested under the lock would
        deadlock a non-reentrant `threading.Lock`). The payload is deep-copied here, and
        the caller is handed a fresh copy back: the jsonl store re-parses bytes on read, so
        a store must return a snapshot, never something a later mutation can reach back into."""
        self._lamport += 1
        data = copy.deepcopy(data or {})
        ev = _Event(
            kind,
            subject,
            data,
            agent,
            self._lamport,
            _eid(kind, subject, data, agent, self._lamport),
        )
        self._shards.setdefault(f"{agent}.jsonl", []).append(ev)
        return ev

    def _events(self) -> list[_Event]:
        """The sorted, de-duplicated stream. The caller holds the lock, so the shard list
        it iterates is the one `append`/`compact` mutated, never one deleted mid-iteration."""
        seen: set[str] = set()
        out: list[_Event] = []
        for shard in sorted(self._shards):
            for ev in self._shards[shard]:
                if ev.id in seen:
                    continue
                seen.add(ev.id)
                out.append(ev)
        out.sort(key=_Event.sort_key)
        return out

    def read_from(self, offset: int = 0):
        with self._lock:
            return [copy.deepcopy(e) for e in self._events() if e.lamport > offset]

    def shards(self) -> list[str]:
        with self._lock:
            return sorted(self._shards)

    def snapshot(self) -> list[_Event]:
        with self._lock:
            return [copy.deepcopy(e) for e in self._events()]

    def compact(self) -> int:
        with self._lock:
            names = [n for n in sorted(self._shards) if self._shards[n]]
            merged = self._shards.get(f"{self._default_agent}.jsonl", [])
            rest = [n for n in names if n != f"{self._default_agent}.jsonl"]
            for name in rest:
                merged.extend(self._shards[name])
                del self._shards[name]
            if rest:
                self._shards[f"{self._default_agent}.jsonl"] = merged
            return len(rest)

    def blob_put(self, ref: str, raw: bytes) -> None:
        if not _is_hex_ref(ref) or _address(raw) != ref:
            raise ValueError(f"{ref!r} is not the address of these bytes")
        with self._lock:
            self._blobs[ref] = raw

    def blob_get(self, ref: str) -> bytes | None:
        # A dict read under the GIL; the write is one dict assignment under the lock.
        return self._blobs.get(ref)

    def blob_refs(self) -> Iterator[str]:
        with self._lock:
            refs = sorted(self._blobs)
        yield from refs

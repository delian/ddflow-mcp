"""The append-only event log — Orchard's single source of truth.

Everything else in this package is a *projection*: the SQLite index, the markdown
boards, the lessons search table, the replay bundle. Delete any of them and
``orchard rebuild`` re-derives them from the log alone. That inversion is what buys
the four properties this system is built for:

* **Crash recovery.** The last event for an item says exactly where it stopped, and
  a lease that stops being renewed expires on its own. There is no half-written
  state machine to repair, because state is never *written* — only folded.
* **Merge without conflict.** Each agent appends to its OWN shard file, so two
  agents on two branches never touch the same bytes. Merging branches is a union of
  files; re-folding the union is deterministic.
* **Reproduction from logs alone.** Operator prompts are events. Replaying the log
  reproduces the decision history that built the repo.
* **Auditability.** Event ids are content addresses, so an event cannot be edited
  after the fact without changing its id and orphaning everything that cites it.

Ordering. Events carry a Lamport counter, and the total order is
``(lamport, agent_id, event_id)``. Lamport gives causality (an event I wrote after
seeing yours sorts after yours); the agent id and content hash break ties
deterministically so two machines folding the same set get the same answer. Wall-clock
``ts`` is recorded for humans and is explicitly NOT the sort key — clock skew between
machines sharing an NFS checkout would otherwise reorder history.

Durability. Appends are serialised by ``flock`` on a single lock file and followed by
``fsync``. POSIX O_APPEND atomicity is *not* relied upon: NFS has no append operation,
so the client does seek-then-write and two concurrent appends can interleave. The lock
is the mechanism; it was measured working on this project's nfs4.2 mount (see
``probes/probe_01_nfs_lock_primitives.py``).
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import socket
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

#: Initial bytes read when seeking a shard's last line, doubled until a full line is
#: found. One page: large enough that a single read almost always suffices, small
#: enough that the tail read stays cheap on NFS (measured ~36x local I/O cost).
TAIL_WINDOW_BYTES = 4096

#: Ceiling on that search. An event larger than this is a bug elsewhere, and the bound
#: is what keeps the Lamport clock read O(shards) rather than O(bytes).
TAIL_MAX_BYTES = 65536


def _kinds() -> frozenset[str]:
    """The event vocabulary, DERIVED from ``model.HANDLERS``.

    Declaring it here as well would make the vocabulary and its interpretation two
    lists that nothing forces to agree -- a kind could be declared and never handled
    (folding silently to "nothing happened"), or handled and never declared (rejected
    at append time). Imported lazily because ``model`` imports this module.
    """
    from .model import HANDLERS

    return frozenset(HANDLERS)


#: Kinds that carry operator intent and must survive every compaction, because they
#: are the input to `orchard replay` — the from-scratch reconstruction path.
PROVENANCE_KINDS: frozenset[str] = frozenset(
    {
        "session.started",
        "session.prompt",
        "session.note",
        "session.ended",
        "research.recorded",
        "lesson.recorded",
        "phase.added",
        "task.added",
    }
)


def utcnow() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def canonical(obj: Any) -> str:
    """Stable JSON: sorted keys, no spaces. The input to every content hash."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class Event:
    kind: str
    subject: str
    data: dict[str, Any] = field(default_factory=dict)
    agent: str = ""
    lamport: int = 0
    ts: str = ""
    id: str = ""
    schema: int = SCHEMA_VERSION

    def body(self) -> dict[str, Any]:
        """The hashed part. `id` is excluded (it is the hash) but everything else is
        in, including lamport and agent — so the same logical event emitted twice by
        two agents is two distinct events, which is correct: they are two claims."""
        return {
            "agent": self.agent,
            "data": self.data,
            "kind": self.kind,
            "lamport": self.lamport,
            "schema": self.schema,
            "subject": self.subject,
            "ts": self.ts,
        }

    def compute_id(self) -> str:
        return "e" + hashlib.blake2b(canonical(self.body()).encode(), digest_size=12).hexdigest()

    def to_json(self) -> str:
        d = self.body()
        d["id"] = self.id or self.compute_id()
        return canonical(d)

    @staticmethod
    def from_json(line: str) -> Event:
        d = json.loads(line)
        return Event(
            kind=d["kind"],
            subject=d.get("subject", ""),
            data=d.get("data", {}),
            agent=d.get("agent", ""),
            lamport=int(d.get("lamport", 0)),
            ts=d.get("ts", ""),
            id=d.get("id", ""),
            schema=int(d.get("schema", 1)),
        )

    def sort_key(self) -> tuple[int, str, str]:
        return (self.lamport, self.agent, self.id or self.compute_id())


def default_agent_id() -> str:
    return f"{socket.gethostname().split('.')[0]}-{os.getpid()}"


#: Transaction depth per (pid, lock path), NOT per EventLog instance.
#: `fcntl.flock` is per-open-file-description, so a second `os.open` of the same file in
#: the same process blocks forever against the lock this process already holds. Two
#: EventLog objects for one repo in one process is entirely reasonable and happens
#: today, so the guard has to be keyed on what actually identifies the lock.
_HELD: dict[tuple[int, str], int] = {}


def _held_key(path: Path) -> tuple[int, str]:
    return (os.getpid(), str(path))


@contextlib.contextmanager
def _flock(path: Path, timeout_s: float) -> Iterator[None]:
    """Exclusive advisory lock, with a bounded wait and a real error on timeout.

    The lock file is created once and NEVER atomically replaced: renaming over a lock
    file puts two holders on two different inodes, each believing it is exclusive.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    deadline = time.monotonic() + timeout_s
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"could not acquire {path} within {timeout_s}s; "
                        f"another agent may be wedged — check `orchard doctor`"
                    ) from None
                time.sleep(0.02)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


class EventLog:
    """Sharded append-only log rooted at ``<root>/.orchard/events``.

    One ``<agent-id>.jsonl`` per agent. Readers take no lock: a torn final line is
    possible in principle on a crash mid-append, so ``read_all`` skips unparseable
    lines and counts them, rather than failing the whole system for one bad byte.
    A torn line is reported by ``orchard doctor``.
    """

    def __init__(self, root: Path, agent_id: str = "", *, lock_timeout_s: float = 30.0) -> None:
        self.root = Path(root)
        self.dir = self.root / ".orchard" / "events"
        self.agent_id = agent_id or default_agent_id()
        self.lock_path = self.root / ".orchard" / "events.lock"
        self.lock_timeout_s = lock_timeout_s
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lamport = 0
        self.skipped_lines = 0

    # -- shard paths ---------------------------------------------------------------
    @property
    def shard(self) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in self.agent_id)
        return self.dir / f"{safe}.jsonl"

    def shards(self) -> list[Path]:
        return sorted(self.dir.glob("*.jsonl"))

    # -- transactions ---------------------------------------------------------------
    @contextlib.contextmanager
    def transaction(self) -> Iterator[EventLog]:
        """Hold the append lock across a read-decide-append sequence.

        Lease acquisition is check-then-act: "is anyone holding this? no -> claim it".
        Without a lock spanning BOTH halves, two agents both read "free" and both
        claim, which is the exact race this whole coordination layer exists to stop.
        Re-entrant by depth count, keyed on (pid, lock path) rather than on this object,
        because flock is per-open-file-description: a SECOND EventLog instance in the
        same process would otherwise deadlock against the lock the first one holds.
        """
        key = _held_key(self.lock_path)
        if _HELD.get(key, 0):
            _HELD[key] += 1
            try:
                yield self
            finally:
                _HELD[key] -= 1
            return
        with _flock(self.lock_path, self.lock_timeout_s):
            _HELD[key] = 1
            try:
                yield self
            finally:
                _HELD.pop(key, None)

    # -- writing -------------------------------------------------------------------
    def append(
        self,
        kind: str,
        subject: str,
        data: dict[str, Any] | None = None,
        *,
        observed: Iterable[Event] = (),
    ) -> Event:
        """Append one event. Returns it with id and lamport filled in.

        ``observed`` lets a caller declare events it has seen but not folded, so the
        Lamport clock advances past them even when the caller read a filtered view.
        """
        if kind not in _kinds():
            raise ValueError(
                f"unknown event kind {kind!r}; add a handler to model.HANDLERS deliberately"
            )
        ctx = (
            contextlib.nullcontext()
            if _HELD.get(_held_key(self.lock_path), 0)
            else _flock(self.lock_path, self.lock_timeout_s)
        )
        with ctx:
            # Re-read inside the lock: another agent may have advanced the clock.
            high = self._highest_lamport()
            for e in observed:
                high = max(high, e.lamport)
            self._lamport = max(self._lamport, high) + 1
            ev = Event(
                kind=kind,
                subject=subject,
                data=dict(data or {}),
                agent=self.agent_id,
                lamport=self._lamport,
                ts=utcnow(),
            )
            ev = Event(**{**ev.__dict__, "id": ev.compute_id()})
            line = ev.to_json() + "\n"
            fd = os.open(self.shard, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o644)
            try:
                os.write(fd, line.encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
        return ev

    def _highest_lamport(self) -> int:
        """Cheapest correct clock read: last line of each shard.

        Lamport values are non-decreasing WITHIN a shard because a shard has exactly
        one writer, so the tail carries that shard's maximum. Reading tails is O(shards)
        rather than O(events) — on NFS, where a full re-read measured ~36x slower than
        local, that difference is the difference between usable and not.
        """
        high = 0
        for p in self.shards():
            tail = _last_line(p)
            if not tail:
                continue
            try:
                high = max(high, int(json.loads(tail).get("lamport", 0)))
            except (ValueError, TypeError):
                continue
        return high

    # -- reading -------------------------------------------------------------------
    def read_all(self) -> list[Event]:
        out: list[Event] = []
        self.skipped_lines = 0
        for p in self.shards():
            try:
                text = p.read_text("utf-8", errors="replace")
            except FileNotFoundError:
                continue
            for raw in text.splitlines():
                line = raw.strip()
                if not line:
                    continue
                try:
                    out.append(Event.from_json(line))
                except (json.JSONDecodeError, KeyError):
                    self.skipped_lines += 1
        # De-duplicate by content address: merging two branches can bring the same
        # event in twice via two shard copies, and a union must be idempotent.
        seen: set[str] = set()
        uniq: list[Event] = []
        for e in sorted(out, key=lambda e: e.sort_key()):
            eid = e.id or e.compute_id()
            if eid in seen:
                continue
            seen.add(eid)
            uniq.append(e)
        return uniq

    def verify(self) -> list[str]:
        """Integrity check: every event's id must equal the hash of its body."""
        problems = []
        for e in self.read_all():
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

"""The event record itself — pure, and deliberately separate from the log that holds it.

An `Event` is a value: a kind, a subject, a payload, a Lamport stamp and a
content-addressed id. Nothing here touches a disk. That is what lets `core.model.fold`
and `core.schedule` be pure, and purity there is why every readiness, gating and
recovery rule in this system is testable without a fixture.

`infra.log.EventLog` is the other half — the append-only, flock-serialised, per-agent
sharded store that these records live in. Splitting them was not tidiness: `core`
imported `infra` to get this dataclass, which inverted the dependency that makes the
domain testable, and the layering test caught it the moment the layers were named.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
#:
#: Architectural decisions belong here and were missing, which made `replay` drop every
#: one of them: the two decision renderers in `session._REPLAY_RENDERERS` were
#: unreachable, and a superseded decision — the context, the rejected alternatives, the
#: reason for the reversal — existed nowhere in the reconstruction. Live decisions still
#: showed up in the brief because that reads folded state, so the loss was invisible
#: exactly where it mattered: rebuilding from the log alone.
#:
#: `tests/test_provenance_complete.py` now pins this set against the replay renderers,
#: so adding a renderer without adding its kind fails.
PROVENANCE_KINDS: frozenset[str] = frozenset(
    {
        "session.started",
        "session.prompt",
        "session.note",
        "session.ended",
        "research.recorded",
        "lesson.recorded",
        "decision.recorded",
        "decision.superseded",
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

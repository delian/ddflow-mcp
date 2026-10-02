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

#: Initial bytes read when seeking a shard's last line (`EventLog.head`'s fingerprint),
#: doubled until a full line is found. One page: large enough that a single read almost
#: always suffices, small enough that the tail read stays cheap on NFS (measured ~36x
#: local I/O cost). The Lamport clock no longer reads tails -- see
#: `EventLog._highest_lamport` for why a tail is not a shard's maximum.
TAIL_WINDOW_BYTES = 4096

#: Ceiling on that search. An event larger than this is a bug elsewhere, and the bound
#: is what keeps the fingerprint O(shards) rather than O(bytes).
TAIL_MAX_BYTES = 65536


#: The event VOCABULARY is not declared here. It is derived from `model.HANDLERS` by
#: `model.known_kinds()`, because a kind nothing interprets must not be appendable and a
#: kind nothing declares must not fold silently to "nothing happened" — one list, in the
#: module that owns the handlers.
#:
#: `_kinds()` used to live here and import `model` lazily, which made `events` and `model`
#: a MUTUALLY importing pair: `model` imports `Event` eagerly (it is in every signature),
#: `events` imported `HANDLERS` inside a function. One eager edge in a mutual pair loads
#: today and becomes an ImportError at startup the moment somebody makes the other eager.
#: Moving the derivation to `model` removes the pair rather than balancing it (B127).

#: Kinds that carry operator intent and must survive every compaction, because they
#: are the input to `ddflow replay` — the from-scratch reconstruction path.
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
        # B191: which of two rival adds an item IS. It carries the kept definition, so
        # a log that kept the adds and dropped this would fold the contest back open.
        # (No replay renderer yet: `replay` skips it, as it does any kind without one.)
        "item.resolved",
        # D-no-duplicates: what an agent ADDED to an existing record, verbatim, and which
        # records it judged the same, related or distinct -- operator-visible intent.
        "record.extended",
        "link.recorded",
        # D-export-agent-enable: who selected a generated document, and the operator's veto
        # (a lock) on it. A compaction that dropped these would lift a lock silently.
        "export.enabled",
        "export.disabled",
        "export.acknowledged",
    }
)


def utcnow() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def canonical(obj: Any) -> str:
    """Stable JSON: sorted keys, no spaces. The input to every content hash."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


#: The keep-a-changelog categories an item.completed / bug.fixed `changelog` field may carry
#: (decision D-export (4)).
CHANGELOG_CATEGORIES = ("Added", "Changed", "Deprecated", "Removed", "Fixed", "Security")
#: `--changelog skip` / `internal`: the entry must not appear in the changelog.
CHANGELOG_SKIP_WORDS = ("skip", "internal")


def parse_changelog(text: str) -> dict[str, Any]:
    """`"Added: text"` -> {category, line, skip}; `skip`/`internal` -> a skip marker.
    Case-insensitive; an unknown category or an empty line raises ValueError naming the list."""
    t = (text or "").strip()
    if t.lower() in CHANGELOG_SKIP_WORDS:
        return {"category": "", "line": "", "skip": True}
    head, sep, rest = t.partition(":")
    by_name = {c.lower(): c for c in CHANGELOG_CATEGORIES}
    if sep and head.strip().lower() in by_name and rest.strip():
        return {"category": by_name[head.strip().lower()], "line": rest.strip(), "skip": False}
    raise ValueError(
        f"--changelog {t!r}: expected 'Category: text' with Category one of "
        f"{', '.join(CHANGELOG_CATEGORIES)}, or 'skip' / 'internal' to keep it out of the changelog"
    )


def changelog_of(raw: Any) -> dict[str, Any]:
    """The folded form of an event's `changelog` key: a well-formed dict, else {}. Lenient on
    purpose -- a newer or hand-written shape must never break the fold."""
    if not isinstance(raw, dict):
        return {}
    cat, line = raw.get("category", ""), raw.get("line", "")
    if raw.get("skip") is True:
        return {"category": "", "line": "", "skip": True}
    if cat in CHANGELOG_CATEGORIES and isinstance(line, str) and line.strip():
        return {"category": cat, "line": line.strip(), "skip": False}
    return {}


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

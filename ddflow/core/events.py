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

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from . import clock
from .digest import content_digest

SCHEMA_VERSION = 1

#: Initial bytes read when seeking a shard's last line (`EventLog.mark`'s clock),
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
        # D-upgrade-skew-guard: the operator let an OLDER ddflow write to a newer log, and
        # why. An override a compaction dropped would leave the marked events unexplained.
        "skew.overridden",
        # D-upgrade-model: an applied upgrade (what was changed, who confirmed what and the
        # backup holding the originals) explains why files and config differ from the last
        # release; a compaction that dropped it would leave that unexplained.
        "upgrade.applied",
        # D-sched-no-daemon: a scheduled job's definition is operator intent like a
        # decision; a compaction that dropped it would silently stop the job.
        "schedule.defined",
        "schedule.updated",
        "schedule.removed",
        # B-uni-def-records: a managed definition (doc type, schedule, trigger, skill,
        # agent, research claim, rule) and every revision of it is operator intent.
        "def.recorded",
        "def.updated",
        "def.retired",
        "def.superseded",
        "def.merged",
    }
)


#: The version stamp (decisions D-upgrade-event-kinds, D-upgrade-skew-guard). `ddflow.seen`
#: says which ddflow version has worked on this log; `skew.overridden` records that an OLDER
#: one was let write anyway; `upgrade.applied` records an applied upgrade. An older ddflow
#: skips all three with a note (the fold is non-strict), so they never break a reader.
SEEN_KIND = "ddflow.seen"
SKEW_OVERRIDDEN_KIND = "skew.overridden"
UPGRADE_APPLIED_KIND = "upgrade.applied"
#: A versioned data repair was applied (`services.repairs`, B-upgrade.5-repairs): the repair's
#: id and the keys of the findings it settled, so the same findings are never offered again.
#: Like the stamp kinds, an older ddflow skips it with a note.
REPAIR_APPLIED_KIND = "repair.applied"
#: The key a session-scoped skew override adds to the `data` of every event written under it:
#: the version of the (older) ddflow that wrote it. Shown by history, replay and doctor.
OLDER_MARK = "older_ddflow"

_PRE_RANK = {"dev": 0, "a": 1, "alpha": 1, "b": 2, "beta": 2, "c": 3, "rc": 3, "pre": 3}
_VERSION_PARTS = re.compile(r"^\s*v?(\d+(?:\.\d+)*)(.*)$", re.DOTALL)


def version_key(version: str) -> tuple:
    """A release version as something comparable: `0.1.10` sorts above `0.1.9`, `0.2` equals
    `0.2.0`, and a pre-release (`0.2.0rc1`, `0.2.0.dev3`) sorts below its release. A version
    that does not start with digits is `()`, which sorts lowest and is never "older" (see
    `is_older`)."""
    m = _VERSION_PARTS.match(version if isinstance(version, str) else "")
    if not m:
        return ()
    nums = [int(p) for p in m.group(1).split(".")]
    while len(nums) > 1 and nums[-1] == 0:
        nums.pop()
    rest = m.group(2).strip()
    if not rest:
        return (tuple(nums), (1,))
    # dev < alpha < beta < rc, then the number after the tag: `0.2.0.dev3` < `0.2.0a1` < `0.2.0rc1`.
    tag = re.match(r"[.\-_]*([A-Za-z]*)[.\-_]*(\d*)", rest)
    assert tag is not None  # every group is optional: it matches the empty string
    rank = _PRE_RANK.get(tag.group(1).lower(), 0)
    return (tuple(nums), (0, rank, int(tag.group(2) or 0)))


def is_older(version: str, than: str) -> bool:
    """True when `version` is a strictly older release than `than`. False when either is
    not a parsable version: an unknown version is never a reason to refuse a write."""
    a, b = version_key(version), version_key(than)
    return bool(a and b and a < b)


class SkewRefused(Exception):
    """An OLDER ddflow was asked to write to a log a newer one has worked on (exit 3).
    Carries the remedy in its message; not an error in the caller's arguments."""

    exit_code = 3  # REFUSED (`core.outcome.exit_for`)


@dataclass(frozen=True)
class StampFacts:
    """What the log says about version skew, for one (agent, running version)."""

    #: The highest version any `ddflow.seen` stamp carries, "" when the log has none.
    highest: str = ""
    #: Who stamped it.
    highest_by: str = ""
    #: This agent has already stamped THIS running version.
    seen_by_me: bool = False
    #: An override of this agent's open session that covers this running version and the
    #: log's current highest stamp, or None.
    override: Event | None = None
    #: The agent's open session ("" when it has none).
    session: str = ""
    #: The running version is OLDER than the log's highest stamp, or this ddflow writes an
    #: older FORMAT level than the highest one a stamp carries (`format_skewed`): the log is
    #: ahead of this code in either case.
    skewed: bool = False
    #: The highest `format_level` any `ddflow.seen` stamp carries, 0 when none does (a stamp
    #: from before the field says nothing about format).
    highest_format: int = 0
    #: The version and agent of the stamp that carries `highest_format` ("" when none does):
    #: who to name, and what to upgrade to, when it is the format that is ahead.
    format_version: str = ""
    format_by: str = ""
    #: Only the format level is behind: the running version is not older than the log's
    #: highest stamp (a branch that changed a format without a version bump).
    format_skewed: bool = False


def _open_session(started: dict[str, tuple[int, str]], ended: set[str]) -> str:
    live = [(pos, sid) for sid, pos in started.items() if sid not in ended]
    return max(live)[1] if live else ""


def _format_of(data: dict[str, Any]) -> int:
    """The `format_level` a stamp carries: a positive integer, else 0 (says nothing)."""
    raw = data.get("format_level")
    return raw if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 1 else 0


def stamp_facts(
    events: Iterable[Event], agent: str, version: str, format_level: int | None = None
) -> StampFacts:
    """Fold the stamp kinds out of `events` (any order). One pass, pure.

    ``format_level`` is the running ddflow's FORMAT_LEVEL; with it the facts also say whether
    the log's highest stamped format is ahead of it (`format_skewed`), and a stamp counts as
    this agent's own only when it carries this format level too. Without it, only versions
    are compared (every caller before the format level existed).

    A session-scoped override is one this agent wrote for the session it currently has OPEN
    (the latest `session.started` of its own with no later `session.ended`; "" when it has
    none, in which case it lasts until the agent next opens or ends one), for this running
    version, against the log's current highest stamp. A new session
    -- or a newer stamp -- is therefore refused again, which is the point of "session-scoped,
    not per command"."""
    highest, highest_by, seen_by_me = "", "", False
    highest_format, format_version, format_by = 0, "", ""
    started: dict[str, tuple[int, str]] = {}
    ended: set[str] = set()
    overrides: list[Event] = []
    last_session_event = 0
    for e in events:
        k = e.kind
        if e.agent == agent and k in ("session.started", "session.ended"):
            last_session_event = max(last_session_event, e.lamport)
        if k == SEEN_KIND:
            v = e.data.get("version")
            if not isinstance(v, str) or not version_key(v):
                continue  # a stamp that names no version says nothing about skew
            # (key, text): two spellings of one version resolve the same way in any order.
            if (version_key(v), v) > (version_key(highest), highest):
                highest, highest_by = v, e.agent
            level = _format_of(e.data)
            if (level, version_key(v), v) > (
                highest_format,
                version_key(format_version),
                format_version,
            ):
                highest_format, format_version, format_by = level, v, e.agent
            if e.agent == agent and v == version:
                seen_by_me = (
                    seen_by_me or format_level is None or _format_of(e.data) == format_level
                )
        elif k == "session.started" and e.agent == agent:
            started[e.subject] = max(started.get(e.subject, (0, "")), (e.lamport, e.id))
        elif k == "session.ended":
            ended.add(e.subject)
        elif k == SKEW_OVERRIDDEN_KIND and e.agent == agent:
            overrides.append(e)
    session = _open_session(started, ended)
    format_skewed = format_level is not None and highest_format > format_level
    override = None
    for e in sorted(overrides, key=Event.sort_key):
        d = e.data
        if (
            d.get("session", "") == session
            and d.get("running") == version
            and d.get("log_version") == highest
            # An override recorded against a format covers exactly that format, whatever this
            # ddflow's own level is now; one recorded without a format covers a log whose
            # format is not ahead of this ddflow.
            and (
                d.get("log_format", 0) == highest_format
                if d.get("log_format")
                else not format_skewed
            )
            # An override made with no session open ends when the agent opens or ends one.
            and (session or e.lamport > last_session_event)
        ):
            override = e
    version_skewed = bool(highest) and is_older(version, highest)
    return StampFacts(
        highest,
        highest_by,
        seen_by_me,
        override,
        session=session,
        skewed=version_skewed or format_skewed,
        highest_format=highest_format,
        format_version=format_version,
        format_by=format_by,
        format_skewed=format_skewed and not version_skewed,
    )


def utcnow() -> str:
    return clock.now_iso()


def canonical(obj: Any) -> str:
    """Stable JSON: sorted keys, no spaces. The input to every content hash."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_digest(obj: Any, *, size: int = 12) -> str:
    """blake2b (``size`` bytes, hex) of ``obj``'s `canonical` form: an event's id, and a
    definition record's content digest (`core.defs`). One hash for both, so the same
    content always reads as the same digest."""
    return content_digest(canonical(obj), "blake2b", size=size)


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
        return "e" + canonical_digest(self.body())

    def to_json(self) -> str:
        d = self.body()
        d["id"] = self.id or self.compute_id()
        return canonical(d)

    @staticmethod
    def from_json(line: str) -> Event:
        d = json.loads(line)
        data = d.get("data", {})
        if not isinstance(data, dict):
            # Not an event: every reader of a payload (the fold, doctor, the stamp guard)
            # takes it as a mapping. The log reader counts the line as unreadable, which
            # doctor reports, instead of one bad line breaking every one of those readers.
            raise TypeError(f"event data is {type(data).__name__}, not an object")
        return Event(
            kind=d["kind"],
            subject=d.get("subject", ""),
            data=data,
            agent=d.get("agent", ""),
            lamport=int(d.get("lamport", 0)),
            ts=d.get("ts", ""),
            id=d.get("id", ""),
            schema=int(d.get("schema", 1)),
        )

    def sort_key(self) -> tuple[int, str, str]:
        return (self.lamport, self.agent, self.id or self.compute_id())

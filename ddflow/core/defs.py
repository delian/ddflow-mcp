"""One managed-definition record for every kind of definition a project keeps.

Doc types, schedules, triggers, skills, agents, research claims and rules are each a
DEFINITION: a named document an operator or agent writes, revises, retires, replaces with
another, or merges into another. R-unify found about eight planned families each about to
add its own recorded/updated/removed events, its own fold, provenance, digest and its own
edit/retire/supersede/merge path. This is the one of each they use instead:

* five events -- ``def.recorded`` (a whole definition), ``def.updated`` (some fields),
  ``def.retired``, ``def.superseded`` (replaced by another of its kind) and ``def.merged``
  (folded into another of its kind) -- with one envelope: ``{kind, id, digest, source,
  provenance}`` plus ``fields`` where the content changes;
* one fold (`handlers/defs.py`), into `State.defs`, keyed ``kind:id`` (`key`), each
  record carrying its history;
* one write path, `api.defs`, which runs the add-time duplicate check and mints nothing
  the caller did not name: a definition's id is its name.

Pure: no I/O, like the rest of `core`. What a family's fields MEAN is the family's own
business; here they are an opaque mapping whose digest says when two versions differ.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .events import canonical_digest

#: The definition kinds, and what each is. A family starts writing its definitions here
#: by name; a kind not listed is refused at the write path (`api.defs`), so a typo cannot
#: start a family of its own.
DEF_KINDS: dict[str, str] = {
    "doctype": "a document type: what a generated or checked document is made of",
    "schedule": "a scheduled job: what runs, how often, on what",
    "trigger": "a trigger: the condition that files remediation work",
    "skill": "an agent skill: instructions an agent loads on demand",
    "agent": "a subagent definition: a role, its prompt and its tools",
    "claim": "a research claim: a falsifiable statement and its probe",
    "rule": "a project rule: a constraint agents are held to",
}

ACTIVE = "active"
RETIRED = "retired"
SUPERSEDED = "superseded"
MERGED = "merged"
#: Every status a definition can be in. Only an ACTIVE one may be revised, retired,
#: superseded or merged, or be the record another is superseded by or merged into.
STATUSES = (ACTIVE, RETIRED, SUPERSEDED, MERGED)

#: The event kinds, in the order a definition's life usually runs.
EVENT_KINDS = ("def.recorded", "def.updated", "def.retired", "def.superseded", "def.merged")


def key(kind: str, rid: str) -> str:
    """A definition's key in `State.defs` and the subject of its events: ``kind:id``. Two
    families may use the same name (a schedule and a skill both called ``nightly``)."""
    return f"{kind}:{rid}"


def digest(fields: dict[str, Any]) -> str:
    """The content digest of a definition's fields: equal fields, equal digest."""
    return canonical_digest(fields, size=16)


@dataclass
class DefRecord:
    """A definition as the log has it now, and how it got there."""

    kind: str
    id: str
    fields: dict[str, Any] = field(default_factory=dict)
    digest: str = ""
    #: Where the definition was read from: a file path or URL, "" when it was written here.
    source: str = ""
    #: Who and what produced it: {"by", "via", ...}; the latest revision's.
    provenance: dict[str, Any] = field(default_factory=dict)
    status: str = ACTIVE
    #: The definition that replaced this one (superseded) or absorbed it (merged).
    successor: str = ""
    #: Why it was retired, superseded or merged.
    reason: str = ""
    #: Definitions merged INTO this one, oldest first.
    merged_from: list[str] = field(default_factory=list)
    #: First recorded: when and by whom (kept across a re-record).
    at: str = ""
    by: str = ""
    #: Latest change.
    updated_at: str = ""
    #: Every event, oldest first: {"event", "at", "by", "digest", "lamport"} (+ "reason",
    #: "successor" where the event names them).
    history: list[dict[str, Any]] = field(default_factory=list)

    @property
    def key(self) -> str:
        return key(self.kind, self.id)

    @property
    def live(self) -> bool:
        return self.status == ACTIVE

"""The record both rules and decisions are: guidance an agent is handed and held to.

A rule ("snake_case for functions") and a decision ("the index lives in sqlite, because ...")
are the same thing to the code that matters: some text, a SCOPE saying where it applies, a
strength, a lifecycle and an owner. They grew as two parallel stacks (D-unify, Operator
2026-10-06: they must share their internals). This is the one core; `rule` and `decision`
stay the user-facing names and each adds only what is its own (`KindSpec.extras`).

Pure: no I/O. Storage is `store`, the authored-file format `fileformat`, applicability
`resolve`, limits and lint `limits`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

#: The lifecycle, in the order a piece of guidance usually lives it. Only ACCEPTED guidance
#: is handed to anyone; the rest is history.
PROPOSED = "proposed"
ACCEPTED = "accepted"
STATUSES = (PROPOSED, ACCEPTED, "deprecated", "superseded", "retired", "rejected")

#: How hard it binds: advisory text, a warning in gate status and review, or a failing gate.
ENFORCEMENTS = ("advisory", "warn", "block")

DEFAULT_PRIORITY = 50
MAX_PRIORITY = 100


@dataclass(frozen=True)
class Scope:
    """Where guidance applies. Each dimension that is set must hold; guidance with none
    set applies ALWAYS (a project-wide rule, a decision with no globs)."""

    globs: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    gates: tuple[str, ...] = ()

    @property
    def always(self) -> bool:
        return not (self.globs or self.categories or self.gates)


@dataclass(frozen=True)
class KindSpec:
    """What one kind of guidance adds to the shared record.

    ``extras`` names the kind's own fields and their defaults (a decision's context,
    consequences and alternatives; a rule's nothing): they live in `GuidanceRecord.ext`,
    are written to the authored file by name and read back from it. ``section`` is the
    config section holding the kind's limits ("" for a kind with none).
    """

    kind: str
    label: str  # "Rule": the noun that opens a message about the kind
    directory: str  # under .ddflow/: the authored files
    section: str = ""
    #: The level a record applies at unless it says otherwise ("project" for a rule).
    default_level: str = ""
    #: Whether a record of the kind carries created/updated stamps in its file.
    stamped: bool = False
    default_priority: int = DEFAULT_PRIORITY
    default_enforcement: str = "advisory"
    extras: tuple[tuple[str, Any], ...] = ()
    valid_id: Callable[[str], bool] = field(default=bool, compare=False)
    id_hint: str = ""

    @property
    def noun(self) -> str:
        return self.label.lower()


@dataclass
class GuidanceRecord:
    """One piece of guidance, of any kind."""

    id: str
    kind: str
    title: str = ""
    body: str = ""
    scope: Scope = field(default_factory=Scope)
    #: Where it applies by project level (a rule's ``scope = "project"|"phase"|"task"``);
    #: not `scope`, which says WHICH WORK it applies to.
    level: str = ""
    category: str = ""
    tags: list[str] = field(default_factory=list)
    priority: int = DEFAULT_PRIORITY
    enforcement: str = "advisory"
    status: str = ACCEPTED
    owner: str = ""
    #: ISO date the guidance is due for review by; "" when never.
    review_by: str = ""
    sources: list[str] = field(default_factory=list)
    #: Fitness-function checks attached to it (B-uni-guidance-enforce): opaque tables here.
    checks: list[dict[str, Any]] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    #: Who and when: {"created", "updated"} for a file, {"at", "by", ...} for a logged one.
    provenance: dict[str, Any] = field(default_factory=dict)
    #: The kind's own fields (`KindSpec.extras`).
    ext: dict[str, Any] = field(default_factory=dict)

    @property
    def live(self) -> bool:
        """Handed to agents: accepted, and not replaced (a decision names what replaced it)."""
        return self.status == ACCEPTED and not self.ext.get("superseded_by")

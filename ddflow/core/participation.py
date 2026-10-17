"""How a declared record kind takes part in search, recall and the fence (RC1, RC4, RC7).

A `RecordKind` (``api.records``) declares once whether its records are searchable, recallable
and fenced as somebody's words; this is the plain-data copy of that declaration the lower
layers read, so ``infra.store`` (the recall index) and ``services.search`` need no knowledge
of any one kind and never import the API. ``api.records.declare`` fills it; nothing else does.

Pure: no I/O.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from . import provenance as PV

#: Definition kinds a hand-written search source already serves, byte for byte (rules, skills
#: and commands, agents, schedules): a descriptor over one of them is not searched twice.
LEGACY_DEF_KINDS = frozenset({"rule", "skill", "agent", "schedule"})
#: Hits per kind a recall shows at most, unless the descriptor says: a large kind must not
#: crowd lessons and decisions out of the pack.
DEFAULT_RECALL_CAP = 3


@dataclass(frozen=True)
class Declared:
    """One declared kind as search and recall see it."""

    name: str
    def_kind: str
    title: str
    #: The record's searchable text from its fields (title and the descriptor's text fields).
    text: Callable[[Mapping[str, Any]], str]
    search: bool = True
    recall: bool = True
    recall_hint: str = ""
    recall_cap: int = DEFAULT_RECALL_CAP

    @property
    def label(self) -> str:
        return self.name.upper()

    @property
    def hint(self) -> str:
        return self.recall_hint or f"a {self.name} this project recorded"

    @property
    def searched(self) -> bool:
        """Whether `ddflow search` lists it through the declared source (not a legacy one)."""
        return self.search and self.def_kind not in LEGACY_DEF_KINDS


#: The declared kinds by name, in declaration order.
DECLARED: dict[str, Declared] = {}


def declare(kind: Declared) -> Declared:
    """Register ``kind``; its recalled hits are fenced as data, like a decision or a lesson."""
    DECLARED[kind.name] = kind
    if kind.recall:
        PV.TABLE_KIND[kind.name] = kind.name
    return kind


def forget(name: str) -> None:
    """Drop ``name`` (a test's throwaway kind)."""
    DECLARED.pop(name, None)
    PV.TABLE_KIND.pop(name, None)


def recalled() -> list[Declared]:
    return [d for d in DECLARED.values() if d.recall]


def recall_entries() -> tuple[tuple[str, str, str], ...]:
    """``(table, label, hint)`` for each recallable declared kind, the shape of
    ``infra.store.RECALL_SOURCES``: the table is the kind's name."""
    return tuple((d.name, d.label, d.hint) for d in recalled())


def searched_names() -> list[str]:
    return [d.name for d in DECLARED.values() if d.searched]

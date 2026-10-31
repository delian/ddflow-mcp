"""A `RecordKind` descriptor and what it declares.

About twelve families (rules, skills, agents, schedules, triggers, doc types, quotas,
harnesses, ids ...) each planned their own list / show / add / edit / remove / search /
revise, with its own filter flags, its own duplicate check, its own regex search and its
own unbounded payloads (R-unify; D-unify 4). A family declares a `RecordKind` instead.

This module is the declared half: the field spec, the descriptor and the registry, plus
the shipped kinds loaded as data (`RULE`). The seven everyday verbs live in `_verbs.py`,
the MCP resources derived from the descriptor in `_resources.py`; this package's
`__init__` re-exports every name the single module exposed.
"""

from __future__ import annotations

import string
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ...core import defs as D
from ...core import kindrules as KR
from ...core import outcome as O
from ...core import participation as PA
from ...core.sensitivity import LABEL_FIELD

#: The verbs, in the order a surface lists them: the seven of a record's everyday use, then
#: the five that follow it through its life (`api.record_lifecycle`).
VERBS = (
    "list",
    "show",
    "add",
    "edit",
    "remove",
    "search",
    "revise",
    "history",
    "supersede",
    "merge",
    "split",
    "restore",
)
#: Rows a list or a search shows unless ``limit`` says otherwise, and the most it ever shows.
DEFAULT_LIMIT = 25
MAX_LIMIT = 1000
#: Characters of one field kept in a list or search row.
CELL_MAX = 120
#: Elements of an array field kept in a list or search row (the rest is counted).
CELL_ITEMS = 20
#: History entries ``record_show`` returns (the newest).
HISTORY_SHOWN = 20
#: Characters of one record a regex or an exact search looks at (as `services.search`).
MAX_SCAN = 2000
MODES = ("ranked", "exact", "regex")
#: JSON types a field may declare (`surfaces.registry.TYPES` without ``number``).
FIELD_TYPES = ("string", "integer", "boolean", "array")
_CUT = " [...]"


@dataclass(frozen=True)
class FieldSpec:
    """One field of a record: what it is called, its JSON type and what it means.

    ``array`` is a list of strings. ``choices`` limits a string; ``default`` is what an
    ``add`` without the field stores (``None``: leave it out).
    """

    name: str
    type: str = "string"
    help: str = ""
    required: bool = False
    choices: tuple[str, ...] | None = None
    default: Any = None

    def __post_init__(self) -> None:
        if self.type not in FIELD_TYPES:
            raise ValueError(f"field {self.name!r}: type {self.type!r} not in {FIELD_TYPES}")
        if self.choices is not None and self.type != "string":
            raise ValueError(f"field {self.name!r}: only a string has choices")


@dataclass(frozen=True)
class ExportSpec:
    """How a kind's records are exported as chunks (B-rc-rag-export): the fields that are the
    chunk's text, the fields kept as its metadata, and a title template (``{title} ({scope})``;
    ``{id}`` and ``{status}`` are always there)."""

    text: tuple[str, ...] = ()
    meta: tuple[str, ...] = ()
    title: str = "{title}"


@dataclass(frozen=True)
class FeedSpec:
    """How a kind's changes enter the change feed (B-rc-changes): ``binding`` ones must be
    acknowledged, the others inform; ``summary`` is the one-line template."""

    binding: bool = False
    summary: str = "{title}"


def _placeholders(template: str) -> list[str]:
    return [name for _lit, name, _spec, _conv in string.Formatter().parse(template) if name]


#: The label every record may carry, whatever its kind declares; `api.record_sensitivity` checks it.
SENSITIVITY_FIELD = FieldSpec(
    "sensitivity", help="How guarded this record is (the sensitivity vocabulary)."
)


@dataclass(frozen=True)
class RecordKind:
    """What one family of records is, declared once.

    ``name`` is the CLI word and the stem of the MCP tool (``ddflow_<name>``); ``def_kind``
    the kind its records are filed under (`core.defs.DEF_KINDS`). ``title`` is the field
    that names a record in a list and, with ``text``, is what the duplicate check and the
    search read. ``columns`` are the fields a list row carries beside ``id`` and ``status``;
    ``filters`` the fields a list or a search can be narrowed by (a string equal to the
    value, an array containing it). ``verbs`` are the ones offered; ``ops`` replaces a verb
    with the family's own function. ``aliases`` are old CLI words that still work.
    """

    name: str
    def_kind: str
    summary: str
    fields: tuple[FieldSpec, ...]
    title: str = "title"
    text: tuple[str, ...] = ()
    columns: tuple[str, ...] = ()
    filters: tuple[str, ...] = ()
    verbs: tuple[str, ...] = VERBS
    aliases: tuple[str, ...] = ()
    ops: Mapping[str, Callable[..., O.Outcome]] = field(default_factory=dict)
    #: A verb the kind does not offer, with WHY (a proposal is never removed: reject and
    #: expire are its tombstones). B-rc-conformance requires one for every verb not offered.
    declined: Mapping[str, str] = field(default_factory=dict)
    # -- participation, DECLARED here and derived elsewhere: a kind that says so is found
    # by `ddflow search --kind <name>`, recalled under its label and read as an MCP resource
    # with no code of its own (RC1, RC4, RC7). The defaults are the ones a new kind wants.
    #: Searchable by `ddflow search` (a definition kind with a hand-written source is not
    #: searched twice).
    search: bool = True
    #: Recallable: `recall` shows at most ``recall_cap`` of its ACTIVE records under the label
    #: ``NAME``, with ``recall_hint`` as the line telling the reader what to do with one.
    recall: bool = True
    recall_hint: str = ""
    recall_cap: int = PA.DEFAULT_RECALL_CAP
    #: An MCP resource pair: ``ddflow://records/<name>`` and ``ddflow://records/<name>/<id>``.
    resource: bool = True
    #: The fields that say where a record applies (globs, categories, gates, tags, an
    #: ``always`` flag): consumed by B-rc-context. Inert here.
    applies: tuple[str, ...] = ()
    #: Export (B-rc-rag-export) and change-feed (B-rc-changes) declarations. Inert here.
    export: ExportSpec | None = None
    feed: FeedSpec | None = None
    #: ``weakening(before, after)``: why ``after`` is weaker than ``before`` (lower
    #: enforcement, a widened override, a guard removed), or "". A merge refuses to weaken
    #: without ``--allow-weaken``.
    weakening: Callable[[Mapping[str, Any], Mapping[str, Any]], str] | None = None
    #: Lifecycle, links, sources, validators, render template and stamp, declared as data
    #: (`core.kindrules`); enforced by `api.record_rules` on add, edit and revise.
    rules: KR.KindRules | None = None
    #: Whether the kind has CLI words and an MCP tool of its own (the rule kind has, by hand).
    #: A data kind is reached through the API, search, recall and resources until the generic
    #: record surface (SE-kind-data.4) carries it; B-rc-conformance reads this.
    surfaced: bool = True

    def __post_init__(self) -> None:
        names = [f.name for f in self.fields]
        if len(set(names)) != len(names):
            raise ValueError(f"record kind {self.name!r}: duplicate field names")
        wanted = {self.title, *self.text, *self.columns, *self.filters}
        if missing := sorted(wanted - set(names)):
            raise ValueError(f"record kind {self.name!r}: no such field(s) {', '.join(missing)}")
        if bad := [v for v in (*self.verbs, *self.ops, *self.declined) if v not in VERBS]:
            raise ValueError(f"record kind {self.name!r}: unknown verb {bad[0]!r}")
        self._check_declarations(set(names))
        if self.rules is not None and (
            bad := self.rules.declaration_problems(
                {f.name: f.type for f in self.fields}, {f.name: f.choices for f in self.fields}
            )
        ):
            raise ValueError(f"record kind {self.name!r}: {bad[0]}")
        if self.ops and not self.ops.keys() <= set(self.verbs):
            raise ValueError(f"record kind {self.name!r}: ops for a verb that is not offered")
        if bad := [v for v, why in self.declined.items() if not str(why).strip()]:
            raise ValueError(f"record kind {self.name!r}: declining {bad[0]!r} needs a reason")
        if not self.ops and self.def_kind not in D.DEF_KINDS:
            raise ValueError(f"record kind {self.name!r}: {self.def_kind!r} is not a def kind")

    def _check_declarations(self, names: set[str]) -> None:
        """A name in ``applies``, ``export`` or ``feed`` that is not a field (or ``id`` /
        ``status`` in a template) is refused at declaration, like a bad column."""
        named = {"applies": set(self.applies)}
        known = names | {"id", "status"}
        if self.export is not None:
            named["export"] = {*self.export.text, *self.export.meta}
            templates = {"export title": self.export.title}
        else:
            templates = {}
        if self.feed is not None:
            templates["feed summary"] = self.feed.summary
        for what, used in named.items():
            if missing := sorted(used - names):
                raise ValueError(
                    f"record kind {self.name!r}: {what} names no such field(s) {', '.join(missing)}"
                )
        for what, template in templates.items():
            try:
                bad = sorted(set(_placeholders(template)) - known)
            except ValueError as exc:
                raise ValueError(
                    f"record kind {self.name!r}: {what} is not a template: {exc}"
                ) from None
            if bad:
                raise ValueError(
                    f"record kind {self.name!r}: {what} names no such field(s) {', '.join(bad)}"
                )
        if self.recall_cap < 1:
            raise ValueError(f"record kind {self.name!r}: recall_cap must be at least 1")

    @property
    def stored_kind(self) -> bool:
        """Whether its records are definitions (`core.defs`), so search, recall and resources
        can read them; a family whose records live elsewhere supplies its own."""
        return self.def_kind in D.DEF_KINDS

    @property
    def all_fields(self) -> tuple[FieldSpec, ...]:
        """The declared fields and the label every kind may carry (what a surface offers)."""
        return (
            self.fields
            if any(f.name == LABEL_FIELD for f in self.fields)
            else (*self.fields, SENSITIVITY_FIELD)
        )

    def field(self, name: str) -> FieldSpec | None:
        """A declared field; ``sensitivity`` is on every kind (SE-sensitivity.2)."""
        found = next((f for f in self.fields if f.name == name), None)
        return found or (SENSITIVITY_FIELD if name == SENSITIVITY_FIELD.name else None)

    def stored(self, verb: str) -> bool:
        """Whether ``verb`` runs on definition records (no family function replaces it)."""
        return verb in self.verbs and verb not in self.ops


# -- the descriptors -----------------------------------------------------------------

#: The kinds declared so far, by name. A family registers its own with `declare`.
KINDS: dict[str, RecordKind] = {}


def declare(kind: RecordKind) -> RecordKind:
    """Register ``kind`` (a name taken by another descriptor is refused: two families must
    not answer one CLI word)."""
    held = KINDS.get(kind.name)
    if held is not None and held != kind:
        raise ValueError(f"record kind {kind.name!r} is already declared")
    KINDS[kind.name] = kind
    if kind.stored_kind:
        PA.declare(
            PA.Declared(
                name=kind.name,
                def_kind=kind.def_kind,
                title=kind.title,
                text=lambda fields, _k=kind: _text(_k, fields),
                search=kind.search,
                recall=kind.recall,
                recall_hint=kind.recall_hint,
                recall_cap=kind.recall_cap,
            )
        )
    return kind


def forget(name: str) -> None:
    """Undeclare ``name`` (a throwaway kind of a test)."""
    KINDS.pop(name, None)
    PA.forget(name)


def _text(kind: RecordKind, fields: Mapping[str, Any]) -> str:
    """The record's searchable text: its title, then the descriptor's text fields."""
    parts: list[str] = [str(fields.get(kind.title, ""))]
    for name in kind.text:
        v = fields.get(name)
        parts.append(" ".join(v) if isinstance(v, list | tuple) else str(v or ""))
    return "\n".join(p for p in parts if p)

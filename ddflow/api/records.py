"""One record surface, the API half: a `RecordKind` descriptor and the verbs it gets.

About twelve families (rules, skills, agents, schedules, triggers, doc types, quotas,
harnesses, ids ...) each planned their own list / show / add / edit / remove / search /
revise, with its own filter flags, its own duplicate check, its own regex search and its
own unbounded payloads (R-unify; D-unify 4). A family declares a `RecordKind` instead, and
this module is what answers for it:

* ``record_list``    filtered, sorted, bounded rows (columns the descriptor names);
* ``record_show``    one record whole, with its history cut to the newest entries;
* ``record_add``     a NEW record, through the add-time duplicate check (D-no-duplicates);
* ``record_edit``    some fields of an active record, the rest kept;
* ``record_remove``  retire it, with a reason (kept, with its history);
* ``record_search``  ranked, exact or bounded-regex search over the record's own text;
* ``record_revise``  replace the whole record, with a reason, bringing a retired one back.

History, supersede, merge, split and restore are `ddflow.api.record_lifecycle`'s; a kind offers
or declines each, with a reason (``declined``).

The records are definitions (`core.defs`, `api.defs`): the descriptor's ``def_kind`` is the
kind they are filed under, so history, provenance, digest and the duplicate check come
with them. A family whose records live elsewhere (a quota is not a definition) gives
``ops`` instead: a verb found there is called with ``(repo, kind, **arguments)`` and
answers an `Outcome`, and the surfaces generated from the descriptor stay the same.

Every payload is bounded: a list or a search shows at most ``limit`` rows (default
`DEFAULT_LIMIT`, at most `MAX_LIMIT`) and says ``total`` and ``truncated``; a long text in a
row is cut (`CELL_MAX`); a record's history shows its newest `HISTORY_SHOWN` entries.
Counts are exact.

Pure of surfaces: `ddflow.surfaces.records` generates the CLI verbs and the MCP tool from
a `RecordKind`.
"""

from __future__ import annotations

import string
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import ddflow.api._dedupe as DD
import ddflow.api.defs as DEFS
import ddflow.api.record_rules as RR

from ..core import defs as D
from ..core import kindrules as KR
from ..core import outcome as O
from ..core import participation as PA
from ..core import provenance as PV
from ..core import rank, textsim
from ..core.textcut import clip
from ..services.export.safe import redact_text
from ..services.searchcore import SearchError, check_regex
from ._base import _load

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
            bad := self.rules.declaration_problems({f.name: f.type for f in self.fields})
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

    def field(self, name: str) -> FieldSpec | None:
        return next((f for f in self.fields if f.name == name), None)

    def stored(self, verb: str) -> bool:
        """Whether ``verb`` runs on definition records (no family function replaces it)."""
        return verb in self.verbs and verb not in self.ops


#: The verbs every kind has had since the surface began, and why a family not yet on the
#: surface does not offer the others.

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


from .record_kind_data import load_all  # noqa: E402  (needs RecordKind above)

#: The shipped kinds are data: ``ddflow/templates/kinds/*.toml`` (the rule kind first).
RULE = load_all(sys.modules[__name__])[0]


# -- shared helpers ------------------------------------------------------------------


def _event(kind: RecordKind, verb: str) -> str:
    return f"{kind.name}.{verb}"


def _limit(raw: Any) -> tuple[int, str]:
    """``(limit, "")``, or ``(0, why)`` for one that is not a count. None is the default;
    0 is the most a call gives (`MAX_LIMIT`), as in every list; a larger number is cut to it."""
    if raw is None:
        return DEFAULT_LIMIT, ""
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        return 0, f"limit must be a whole number, 0 or more; got {raw!r}"
    return (min(raw, MAX_LIMIT) if raw else MAX_LIMIT), ""


def _cell(value: Any) -> Any:
    if isinstance(value, str):
        return clip(" ".join(value.split()), CELL_MAX + len(_CUT), keep=CELL_MAX, marker=_CUT)
    if isinstance(value, list):
        kept = [_cell(v) for v in value[:CELL_ITEMS]]
        if len(value) > CELL_ITEMS:
            kept.append(f"[+{len(value) - CELL_ITEMS} more]")
        return kept
    return value


def _row(kind: RecordKind, rec: Any) -> dict[str, Any]:
    row: dict[str, Any] = {"id": rec.id, "status": rec.status}
    for name in kind.columns:
        if name in rec.fields:
            row[name] = _cell(rec.fields[name])
    return row


def _matches(rec: Any, filters: Mapping[str, str]) -> bool:
    for name, want in filters.items():
        have = rec.fields.get(name)
        if isinstance(have, list):
            if want not in have:
                return False
        elif have != want:
            return False
    return True


def _filter_problem(kind: RecordKind, filters: Mapping[str, str]) -> str:
    bad = sorted(set(filters) - set(kind.filters))
    if bad:
        offered = ", ".join(kind.filters) or "none"
        return f"cannot narrow {kind.name} by {', '.join(bad)}; the filters are {offered}"
    return ""


def _records(st: Any, kind: RecordKind, *, everything: bool) -> list[Any]:
    return [
        r for _k, r in sorted(st.defs.items()) if r.kind == kind.def_kind and (everything or r.live)
    ]


def _field_problem(spec: FieldSpec, value: Any) -> str:
    """Why ``value`` is not a ``spec`` field, or ""."""
    ok = {
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "array": isinstance(value, list | tuple) and all(isinstance(v, str) for v in value),
    }[spec.type]
    if not ok:
        what = "a list of strings" if spec.type == "array" else f"a {spec.type}"
        return f"{spec.name} must be {what}"
    if spec.choices is not None and value not in spec.choices:
        return f"{spec.name} must be one of {', '.join(spec.choices)}"
    return ""


def _absent(spec: FieldSpec, fields: Mapping[str, Any]) -> bool:
    """Whether a required field is missing: not given, null, or an empty string. A zero, a
    false and an empty list are values."""
    value = fields.get(spec.name)
    return value is None or (spec.type == "string" and not value.strip())


def _checked(kind: RecordKind, fields: Mapping[str, Any], *, whole: bool) -> tuple[dict, str]:
    """``(fields as stored, "")`` or ``({}, why not)``. A whole record gets its defaults and
    must carry every required field; a partial one (an edit) only the ones it names, and a
    null removes one that is not required."""
    out: dict[str, Any] = {}
    for name, value in fields.items():
        spec = kind.field(name)
        if spec is None:
            known = ", ".join(f.name for f in kind.fields)
            return {}, f"{kind.name} has no field {name!r}; the fields are {known}"
        if value is None and not whole and not spec.required:
            out[name] = None
            continue
        if problem := _field_problem(spec, value):
            return {}, problem
        if spec.required and _absent(spec, {name: value}):
            return {}, f"{name} is required: it cannot be blank"
        out[name] = list(value) if spec.type == "array" else value
    if whole:
        for spec in kind.fields:
            if spec.name not in out and spec.default is not None:
                out[spec.name] = list(spec.default) if spec.type == "array" else spec.default
        if missing := [s.name for s in kind.fields if s.required and _absent(s, out)]:
            return {}, f"a {kind.name} needs {', '.join(missing)}"
    return out, ""


def _pre(kind: RecordKind, verb: str, repo: Path, **kw: Any) -> O.Outcome | None:
    """An answer given before the verb runs on definitions: a refusal when ``kind`` does not
    offer ``verb``, what the family's own function answers when it replaces it, else None.
    (Compare with ``is not None``: a refused `Outcome` is falsy.)"""
    if verb not in kind.verbs:
        why = kind.declined.get(verb, "")
        return O.refused(
            _event(kind, verb),
            f"{kind.name} has no {verb!r}"
            + (f" ({why})" if why else "")
            + f"; it offers {', '.join(kind.verbs)}",
        )
    fn = kind.ops.get(verb)
    return None if fn is None else fn(repo, kind, **kw)


def _body(kind: RecordKind, fields: Mapping[str, Any]) -> str:
    """The text the duplicate check reads beside the title."""
    return _text(kind, {k: v for k, v in fields.items() if k != kind.title})


def _text(kind: RecordKind, fields: Mapping[str, Any]) -> str:
    parts: list[str] = [str(fields.get(kind.title, ""))]
    for name in kind.text:
        v = fields.get(name)
        parts.append(" ".join(v) if isinstance(v, list | tuple) else str(v or ""))
    return "\n".join(p for p in parts if p)


# -- the verbs -----------------------------------------------------------------------


def record_list(
    repo: Path,
    kind: RecordKind,
    *,
    filters: Mapping[str, str] | None = None,
    all: bool = False,
    limit: int | None = None,
    agent: str = "",
) -> O.Outcome:
    """The records of ``kind`` (active ones, or every one with ``all``), by id."""
    ev = _event(kind, "list")
    if (
        pre := _pre(kind, "list", repo, filters=filters, all=all, limit=limit, agent=agent)
    ) is not None:
        return pre
    filters = {k: v for k, v in (filters or {}).items() if v not in ("", None)}
    n, why = _limit(limit)
    if why or (why := _filter_problem(kind, filters)):
        return O.refused(ev, why)
    _log, _cfg, st = _load(repo, agent)
    found = [r for r in _records(st, kind, everything=all) if _matches(r, filters)]
    return _page(ev, kind, [_row(kind, r) for r in found], n, filters=filters)


def _page(ev: str, kind: RecordKind, rows: list[dict], n: int, **extra: Any) -> O.Outcome:
    data: dict[str, Any] = {
        "record_kind": kind.name,
        "rows": rows[:n],
        "total": len(rows),
        "shown": min(n, len(rows)),
        "limit": n,
        "truncated": len(rows) > n,
        **extra,
    }
    if not rows:
        return O.nothing(ev, f"No {kind.name} records.", **data)
    return O.ok(ev, **data)


def record_show(repo: Path, kind: RecordKind, rid: str, *, agent: str = "") -> O.Outcome:
    """One record whole; its history is the newest `HISTORY_SHOWN` entries (``history_total``
    is the count)."""
    if (pre := _pre(kind, "show", repo, id=rid, agent=agent)) is not None:
        return pre
    out = DEFS.def_show(repo, kind.def_kind, rid, agent=agent)
    if out.exit != 0:
        return O.failed(
            _event(kind, "show"), f"no {kind.name} {rid!r}", record_kind=kind.name, id=rid
        )
    data = dict(out.data)
    history = data.get("history") or []
    data.update(history=history[-HISTORY_SHOWN:], history_total=len(history), record_kind=kind.name)
    if kind.rules is not None and kind.rules.render:
        data["rendered"] = KR.render(
            kind.rules, rid, str(data.get("status", "")), data.get("fields") or {}
        )
    return O.ok(_event(kind, "show"), **data)


def _exists(st: Any, kind: RecordKind, rid: str) -> Any:
    return st.defs.get(D.key(kind.def_kind, rid))


def record_add(
    repo: Path,
    kind: RecordKind,
    rid: str,
    fields: Mapping[str, Any],
    *,
    answer: DD.Answer | None = None,
    agent: str = "",
) -> O.Outcome:
    """File a NEW record. It runs the add-time duplicate check (``answer`` is the adder's
    word on a possible duplicate, `DD.Answer`); an id already filed is refused, whatever its
    status: ``edit`` changes it and ``revise`` replaces it."""
    ev = _event(kind, "add")
    if (
        pre := _pre(kind, "add", repo, id=rid, fields=fields, answer=answer, agent=agent)
    ) is not None:
        return pre
    stored, why = RR.checked(repo, kind, rid, *_checked(kind, fields, whole=True), agent=agent)
    if why:
        return O.failed(ev, why, record_kind=kind.name, id=rid)
    _log, _cfg, st = _load(repo, agent)
    if (held := _exists(st, kind, rid)) is not None:
        return O.refused(
            ev,
            f"{kind.name} {rid!r} already exists ({held.status}); edit changes it, "
            "revise replaces it",
            record_kind=kind.name,
            id=rid,
        )
    out = DEFS.def_record(
        repo,
        kind.def_kind,
        rid,
        stored,
        title=str(stored.get(kind.title, "")),
        body=_body(kind, stored),
        answer=answer,
        agent=agent,
    )
    return _as(out, kind, "add", rid)


#: How `api.defs` ends a reason about a definition that is no longer active.
_BRING_BACK = "; record it again to bring it back"


def _named(kind: RecordKind, rid: str, reason: str) -> str:
    """``reason`` (worded by `api.defs` for a definition of the storage kind) in the record's
    own name: a ``note`` filed as a ``skill`` definition says note, and ``revise`` brings one
    back. Only the opening words and the closing clause of a known reason are rewritten,
    never text inside the id."""
    dk, name, q = kind.def_kind, kind.name, repr(rid)
    heads = (
        (f"no {dk} definition {q}", f"no {name} {q}"),
        (f"{dk} {q} is ", f"{name} {q} is "),
        (f"{dk} {rid}: ", f"{name} {rid}: "),
        (f"definition id {q} ", f"{name} id {q} "),
        ("retiring a definition ", f"retiring a {name} "),
    )
    for old, new in heads:
        if reason.startswith(old):
            reason = new + reason[len(old) :]
            break
    if reason.endswith(_BRING_BACK):
        reason = reason[: -len(_BRING_BACK)] + "; revise it to bring it back"
    return reason


def _as(out: O.Outcome, kind: RecordKind, verb: str, rid: str) -> O.Outcome:
    """``out`` (an `api.defs` answer) as this surface's: the same data, its own event name
    and the record's kind and id."""
    data = {k: v for k, v in out.data.items() if k not in ("def_kind", "id")}
    data.update(record_kind=kind.name, id=rid)
    ev = _event(kind, verb)
    if out.exit == O.OK:
        return O.ok(ev, **data)
    make = {O.NOTHING: O.nothing, O.REFUSED: O.refused}.get(out.exit, O.failed)
    return make(ev, _named(kind, rid, out.reason), **data)


def record_edit(
    repo: Path, kind: RecordKind, rid: str, fields: Mapping[str, Any], *, agent: str = ""
) -> O.Outcome:
    """Change some fields of an ACTIVE record; a field set to None is removed (unless it is
    required). Nothing named, or nothing changed, is exit 2."""
    ev = _event(kind, "edit")
    if (pre := _pre(kind, "edit", repo, id=rid, fields=fields, agent=agent)) is not None:
        return pre
    if not fields:
        return O.nothing(ev, f"{kind.name} {rid}: no field given to change", id=rid)
    stored, why = RR.checked(repo, kind, rid, *_checked(kind, fields, whole=False), agent=agent)
    if why:
        return O.failed(ev, why, record_kind=kind.name, id=rid)
    return _as(DEFS.def_update(repo, kind.def_kind, rid, stored, agent=agent), kind, "edit", rid)


def record_remove(
    repo: Path, kind: RecordKind, rid: str, *, reason: str, agent: str = ""
) -> O.Outcome:
    """Retire a record: kept, with its history, no longer in force. Needs a reason."""
    if (pre := _pre(kind, "remove", repo, id=rid, reason=reason, agent=agent)) is not None:
        return pre
    return _as(
        DEFS.def_retire(repo, kind.def_kind, rid, reason=reason, agent=agent), kind, "remove", rid
    )


def record_revise(
    repo: Path,
    kind: RecordKind,
    rid: str,
    fields: Mapping[str, Any],
    *,
    reason: str,
    agent: str = "",
) -> O.Outcome:
    """Replace a record WHOLE (not only the fields named), with a reason that its history
    keeps. A retired, superseded or merged record comes back. An id nothing has filed is
    refused: ``add`` files it."""
    ev = _event(kind, "revise")
    if (
        pre := _pre(kind, "revise", repo, id=rid, fields=fields, reason=reason, agent=agent)
    ) is not None:
        return pre
    if not (reason or "").strip():
        return O.failed(ev, "revising a record needs a reason", record_kind=kind.name, id=rid)
    stored, why = RR.checked(repo, kind, rid, *_checked(kind, fields, whole=True), agent=agent)
    if why:
        return O.failed(ev, why, record_kind=kind.name, id=rid)
    _log, _cfg, st = _load(repo, agent)
    prev = _exists(st, kind, rid)
    if prev is None:
        return O.refused(ev, f"no {kind.name} {rid!r}; add files a new one", id=rid)
    out = DEFS.def_record(
        repo,
        kind.def_kind,
        rid,
        stored,
        source=prev.source,
        provenance={"revised": reason.strip()},
        agent=agent,
    )
    return _as(out, kind, "revise", rid)


def _scored(kind: RecordKind, recs: list[Any], query: str, mode: str) -> list[tuple[float, Any]]:
    texts = [_text(kind, r.fields) for r in recs]
    if mode == "regex":
        rx = check_regex(query)
        return [(0.8, r) for r, t in zip(recs, texts, strict=True) if rx.search(t[:MAX_SCAN])]
    if mode == "exact":
        needle = " ".join(query.lower().split())
        return [
            (1.0, r)
            for r, t in zip(recs, texts, strict=True)
            if needle in " ".join(t[:MAX_SCAN].lower().split())
        ]
    qtoks = textsim.tokens(query)
    if not qtoks:
        raise SearchError("nothing searchable in that text (only stop words or ids); use exact")
    scores = rank.tfidf([textsim.tokens(t[: 2 * MAX_SCAN]) for t in texts], qtoks)
    return [(sc, recs[i]) for i, sc in scores.items() if sc > 0]


def record_search(
    repo: Path,
    kind: RecordKind,
    query: str,
    *,
    mode: str = "ranked",
    filters: Mapping[str, str] | None = None,
    all: bool = False,
    limit: int | None = None,
    agent: str = "",
) -> O.Outcome:
    """The records whose text (title and the descriptor's text fields) reads like
    ``query``, best first, then by id. ``regex`` runs through the one safety check; a
    pattern that could take exponential time is refused with the reason."""
    ev = _event(kind, "search")
    if (
        pre := _pre(
            kind,
            "search",
            repo,
            query=query,
            mode=mode,
            filters=filters,
            all=all,
            limit=limit,
            agent=agent,
        )
    ) is not None:
        return pre
    filters = {k: v for k, v in (filters or {}).items() if v not in ("", None)}
    n, why = _limit(limit)
    if not (query or "").strip():
        why = why or "nothing to search for: give some text"
    elif mode not in MODES:
        why = why or f"unknown mode {mode!r}: one of {', '.join(MODES)}"
    if why or (why := _filter_problem(kind, filters)):
        return O.refused(ev, why, query=query)
    _log, _cfg, st = _load(repo, agent)
    pool = [r for r in _records(st, kind, everything=all) if _matches(r, filters)]
    try:
        hits = _scored(kind, pool, query, mode)
    except SearchError as exc:
        return O.refused(ev, str(exc), query=query)
    hits.sort(key=lambda h: (-h[0], h[1].id))
    rows = [
        {**_row(kind, r), "score": round(sc, 4) if mode == "ranked" else None} for sc, r in hits
    ]
    return _page(ev, kind, rows, n, query=query, mode=mode, filters=filters, searched=len(pool))


# -- MCP resources, derived from the descriptor (RC4) --------------------------------

#: A resource URI is ``ddflow://records/<kind>`` (the list) or ``.../<kind>/<id>`` (one).
RESOURCE_PREFIX = "ddflow://records/"
#: Rows the list resource shows (newest first), and characters of one field in either.
RESOURCE_ROWS = 50
RESOURCE_TEXT = 600


def _resource_kinds() -> dict[str, RecordKind]:
    return {k.name: k for k in KINDS.values() if k.resource and k.stored_kind}


def record_resource_listing() -> list[dict[str, str]]:
    """What ``resources/list`` says of each kind that is served as a resource: the list URI,
    with the pattern of the per-record one in its description."""
    return [
        {
            "uri": f"{RESOURCE_PREFIX}{k.name}",
            "name": f"{k.name.capitalize()} records",
            "description": f"{k.summary[:1].upper()}{k.summary[1:]}. "
            f"Newest first; {RESOURCE_PREFIX}{k.name}/{{id}} reads one with its history.",
            "mimeType": "text/markdown",
        }
        for k in _resource_kinds().values()
    ]


def _resource_text(kind: RecordKind, rec: Any, full: bool) -> str:
    """One record as a line (``full`` False) or whole: its fields as DATA inside the fence,
    each cut to `RESOURCE_TEXT`, then its status, successor chain and history digest."""
    head = f"{rec.id}  [{rec.status}] {str(rec.fields.get(kind.title, ''))[:120]}".rstrip()
    if not full:
        return f"- {' '.join(head.split())}"
    body = "; ".join(
        f"{n}: {clip(' '.join(str(v).split()), RESOURCE_TEXT)}" for n, v in rec.fields.items()
    )
    origin = PV.Origin(PV.IMPORTED if rec.source else PV.AGENT, rec.by, rec.source)
    out = [f"# {kind.name} {rec.id}", f"status: {rec.status}"]
    if rec.successor:
        out.append(f"{rec.status} by: {rec.successor}")
    if rec.merged_from:
        out.append(f"absorbed: {', '.join(rec.merged_from)}")
    out.append(PV.fence(kind.name, rec.id, body, origin))
    events = [h["event"] for h in rec.history]
    out.append(
        f"history: {len(events)} entries, first {rec.at[:10]}, last {(rec.updated_at or rec.at)[:10]} "
        f"({', '.join(events[-5:])}); `ddflow {kind.name} history {rec.id} --diff` has them all"
    )
    return "\n".join(out)


def record_resource_read(repo: Path, uri: str, *, agent: str = "") -> str | None:
    """The text of a record resource, redacted, or None when ``uri`` names no served kind or
    record. The list is bounded (`RESOURCE_ROWS`, newest first) and says how many it holds."""
    kinds = _resource_kinds()
    if not uri.startswith(RESOURCE_PREFIX):
        return None
    name, _sep, rid = uri[len(RESOURCE_PREFIX) :].partition("/")
    kind = kinds.get(name)
    if kind is None:
        return None
    _log, cfg, st = _load(repo, agent)
    if rid:
        rec = _exists(st, kind, rid)
        return None if rec is None else redact_text(_resource_text(kind, rec, True), cfg).text
    recs = sorted(
        _records(st, kind, everything=True),
        key=lambda r: (r.updated_at or r.at, r.id),
        reverse=True,
    )
    lines = [
        f"# {kind.name} records",
        f"{len(recs)} in all; newest {min(len(recs), RESOURCE_ROWS)}:",
    ]
    lines += [_resource_text(kind, r, False) for r in recs[:RESOURCE_ROWS]]
    return redact_text("\n".join(lines), cfg).text

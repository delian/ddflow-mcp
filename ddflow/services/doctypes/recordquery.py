"""A declarative record query: ``kind=task state=open tags=todo sort=-priority limit=20``.

No expressions and no code: ``key=value`` words, validated when the document type loads. The
keys are ``kind`` (required), ``state``, ``tags`` (any of), ``phase``, ``since``, ``owner``,
``sort`` (``-`` before a field reverses), ``limit`` and ``fields``; a list is comma separated.
`run` answers over a `Query` with plain rows, in a total order (the sort key, then the id).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import fields as dc_fields
from typing import Any

from ...core import records
from ...core.plain import plain
from ..export.query import ExportError, Query, since_filter

KEYS = ("kind", "state", "tags", "phase", "since", "owner", "sort", "limit", "fields")
MAX_LIMIT = 10_000


class QueryError(ExportError):
    """A query that is not one; the message says which word."""


def _bug_state(b: Any) -> str:
    return "invalid" if b.invalid_at else "fixed" if b.fixed_at else "open"


def _item_owner(i: Any) -> str:
    return i.lease.holder if i.lease and not i.lease.expired_at else ""


@dataclass(frozen=True)
class RecordKind:
    """How one record type is listed: its rows and the computed columns a query filters on."""

    rows: Callable[[Query], list[Any]]
    state: Callable[[Any], str] = lambda r: getattr(r, "state", "")
    owner: Callable[[Any], str] = lambda r: ""
    date: str = ""  # the field `since` compares
    phase: bool = False


def _status(r: Any) -> str:
    return getattr(r, "status", "")


def _live(r: Any) -> str:
    return "forgotten" if getattr(r, "forgotten", "") else "live"


def _items(q: Query) -> list[Any]:
    return [i for i in q.state.items.values() if not i.removed]


def _research(q: Query) -> list[Any]:
    return sorted(q.state.research.values(), key=lambda r: r.id)


def _memories(q: Query) -> list[Any]:
    return sorted(q.state.memories.values(), key=lambda r: r.id)


def _lesson_state(r: Any) -> str:
    return "superseded" if r.superseded_by else "live"


def _session_state(r: Any) -> str:
    return "ended" if r.ended_at else "open"


KINDS: dict[str, RecordKind] = {
    "task": RecordKind(lambda q: q.tasks(), owner=_item_owner, date="created_at", phase=True),
    "phase": RecordKind(lambda q: q.phases(), owner=_item_owner, date="created_at"),
    "item": RecordKind(_items, owner=_item_owner, date="created_at", phase=True),
    "bug": RecordKind(lambda q: q.bugs(), state=_bug_state, date="found_at"),
    "decision": RecordKind(lambda q: q.decisions(), _status, lambda r: r.decided_by, "at"),
    "lesson": RecordKind(lambda q: q.lessons(), _lesson_state, lambda r: r.by, "at"),
    "research": RecordKind(_research, lambda r: r.verdict.lower(), date="at"),
    "session": RecordKind(lambda q: q.sessions(), _session_state, lambda r: r.agent, "started_at"),
    "memory": RecordKind(_memories, _live, lambda r: r.by, "at"),
}


@dataclass(frozen=True)
class RecordQuery:
    kind: str
    state: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    phase: str = ""
    since: str = ""
    owner: str = ""
    sort: str = "id"
    reverse: bool = False
    limit: int = 0
    fields: tuple[str, ...] = ()


def _list(v: str) -> tuple[str, ...]:
    return tuple(x for x in v.split(",") if x)


def parse(text: str) -> RecordQuery:
    """The query a source spells, or `QueryError` naming the word at fault."""
    words: dict[str, str] = {}
    for w in text.split():
        key, eq, val = w.partition("=")
        if not eq or not val:
            raise QueryError(f"query word {w!r} is not key=value")
        if key not in KEYS:
            raise QueryError(f"unknown query key {key!r}. Known: {', '.join(KEYS)}")
        if key in words:
            raise QueryError(f"query key {key!r} is given twice")
        words[key] = val
    kind = words.get("kind", "")
    if kind not in KINDS:
        raise QueryError(f"query kind {kind!r} is not one of {', '.join(KINDS)}")
    spec = KINDS[kind]
    if "phase" in words and not spec.phase:
        raise QueryError(f"query key 'phase' does not apply to kind {kind!r}")
    if "since" in words:
        if not spec.date:
            raise QueryError(f"query key 'since' does not apply to kind {kind!r}")
        try:
            since_filter(words["since"])
        except ExportError as exc:
            raise QueryError(str(exc)) from None
    limit = words.get("limit", "0")
    if not limit.isdigit() or int(limit) > MAX_LIMIT:
        raise QueryError(f"query limit {limit!r} is not a number from 0 to {MAX_LIMIT}")
    sort = words.get("sort", "id")
    names = _columns(kind)
    if sort.lstrip("-") not in names:
        raise QueryError(f"query sort {sort!r} is not a field of {kind}: {', '.join(names)}")
    fields_ = _list(words.get("fields", ""))
    if bad := [f for f in fields_ if f not in names]:
        raise QueryError(f"query field {bad[0]!r} is not a field of {kind}: {', '.join(names)}")
    return RecordQuery(
        kind, _list(words.get("state", "")), _list(words.get("tags", "")),
        words.get("phase", ""), words.get("since", ""), words.get("owner", ""),
        sort.lstrip("-"), sort.startswith("-"), int(limit), fields_,
    )  # fmt: skip


def _columns(kind: str) -> tuple[str, ...]:
    cls = {
        "task": records.Item, "phase": records.Item, "item": records.Item, "bug": records.Bug,
        "decision": records.Decision, "lesson": records.Lesson, "research": records.ResearchNote,
        "session": records.Session, "memory": records.Memory,
    }[kind]  # fmt: skip
    names = [f.name for f in dc_fields(cls) if not f.metadata.get("internal")]
    return tuple(dict.fromkeys([*names, "state", "owner"]))


def run(rq: RecordQuery, q: Query) -> list[dict[str, Any]]:
    """The rows ``rq`` selects, in order, as plain dicts (``state`` and ``owner`` computed)."""
    spec = KINDS[rq.kind]
    after = since_filter(rq.since) if rq.since else None
    rows: list[dict[str, Any]] = []
    for r in spec.rows(q):
        row = plain(r)
        row["state"], row["owner"] = spec.state(r), spec.owner(r)
        if rq.state and row["state"] not in rq.state:
            continue
        if rq.tags and not set(rq.tags) & set(row.get("tags") or ()):
            continue
        if rq.phase and row.get("parent") != rq.phase:
            continue
        if rq.owner and row["owner"] != rq.owner:
            continue
        if after is not None and not after(str(row.get(spec.date) or "")):
            continue
        rows.append(row)
    rows.sort(key=lambda x: str(x["id"]))  # the total order under a stable sort
    rows.sort(key=lambda x: _key(x.get(rq.sort)), reverse=rq.reverse)
    if rq.limit:
        rows = rows[: rq.limit]
    if rq.fields:
        rows = [{k: x[k] for k in rq.fields if k in x} for x in rows]
    return rows


def _key(v: Any) -> tuple[int, Any]:
    """Numbers before text, lists as their text: any two values compare."""
    if isinstance(v, bool):
        return (0, int(v))
    if isinstance(v, (int, float)):
        return (0, v)
    return (1, str(v if v is not None else ""))

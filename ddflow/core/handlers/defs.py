"""The fold of the `def.*` events into `State.defs` (`core.defs` holds the record)."""

from __future__ import annotations

from typing import Any

from ..defs import ACTIVE, MERGED, RETIRED, SUPERSEDED, DefRecord, digest, key
from ..events import Event
from ..records import State


def _target(ev: Event) -> tuple[str, str]:
    """(kind, id) an event is about, from its envelope; the subject when the envelope is
    short (a hand-written event)."""
    kind = str(ev.data.get("kind") or ev.subject.partition(":")[0])
    rid = str(ev.data.get("id") or ev.subject.partition(":")[2])
    return kind, rid


def _note(rec: DefRecord, ev: Event, **extra: Any) -> None:
    entry = {
        "event": ev.kind,
        "at": ev.ts,
        "by": ev.agent,
        "digest": rec.digest,
        "lamport": ev.lamport,
    }
    entry.update({k: v for k, v in extra.items() if v})
    rec.history.append(entry)
    rec.updated_at = ev.ts


def _record(st: State, ev: Event) -> DefRecord:
    """The record an event is about, created when an update or a status change arrives
    before its definition (a shard merged out of order): the definition then replaces the
    fields and keeps the history."""
    kind, rid = _target(ev)
    k = key(kind, rid)
    rec = st.defs.get(k)
    if rec is None:
        rec = st.defs[k] = DefRecord(kind=kind, id=rid, at=ev.ts, by=ev.agent)
        # A merge into this one folded before it existed (shards out of order) still
        # counts: the absorbed records are found by their successor, in either order.
        rec.merged_from = [
            o.id
            for o in st.defs.values()
            if o.kind == kind and o.status == MERGED and o.successor == rid and o.id != rid
        ]
    return rec


def _envelope(rec: DefRecord, ev: Event) -> None:
    if "source" in ev.data:
        rec.source = str(ev.data.get("source") or "")
    prov = ev.data.get("provenance")
    if isinstance(prov, dict):
        rec.provenance = dict(prov)


def h_recorded(st: State, ev: Event) -> None:
    """A whole definition: its fields REPLACE the previous ones, and recording a retired,
    superseded or merged definition again brings it back. When it was first recorded,
    by whom, what was merged into it and its history are kept."""
    rec = _record(st, ev)
    fields = ev.data.get("fields")
    rec.fields = dict(fields) if isinstance(fields, dict) else {}
    rec.digest = str(ev.data.get("digest") or digest(rec.fields))
    rec.status, rec.successor, rec.reason = ACTIVE, "", ""
    _envelope(rec, ev)
    _note(rec, ev)


def h_updated(st: State, ev: Event) -> None:
    """Only the fields the event carries change; a field set to None is removed."""
    rec = _record(st, ev)
    fields = ev.data.get("fields")
    for name, value in (fields if isinstance(fields, dict) else {}).items():
        if value is None:
            rec.fields.pop(name, None)
        else:
            rec.fields[name] = value
    rec.digest = str(ev.data.get("digest") or digest(rec.fields))
    _envelope(rec, ev)
    _note(rec, ev)


def h_retired(st: State, ev: Event) -> None:
    rec = _record(st, ev)
    rec.status, rec.successor = RETIRED, ""
    rec.reason = str(ev.data.get("reason") or "")
    _envelope(rec, ev)
    _note(rec, ev, reason=rec.reason)


def h_superseded(st: State, ev: Event) -> None:
    """Replaced by ``successor``, another definition of the same kind."""
    rec = _record(st, ev)
    rec.status = SUPERSEDED
    rec.successor = str(ev.data.get("successor") or "")
    rec.reason = str(ev.data.get("reason") or "")
    _envelope(rec, ev)
    _note(rec, ev, reason=rec.reason, successor=rec.successor)


def h_merged(st: State, ev: Event) -> None:
    """Folded into ``successor`` (same kind), which records that it absorbed this one."""
    rec = _record(st, ev)
    rec.status = MERGED
    rec.successor = str(ev.data.get("successor") or "")
    rec.reason = str(ev.data.get("reason") or "")
    _envelope(rec, ev)
    _note(rec, ev, reason=rec.reason, successor=rec.successor)
    into = st.defs.get(key(rec.kind, rec.successor))
    if into is not None and rec.id not in into.merged_from:
        into.merged_from.append(rec.id)


#: event kind -> its handler, for `model.HANDLERS`.
DEF_HANDLERS = {
    "def.recorded": h_recorded,
    "def.updated": h_updated,
    "def.retired": h_retired,
    "def.superseded": h_superseded,
    "def.merged": h_merged,
}

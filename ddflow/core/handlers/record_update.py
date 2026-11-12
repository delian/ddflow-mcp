"""`<kind>.updated` for the records that were events of their own kind (DX-update-universal).

One strict handler per kind, built from the catalogue (`core.record_fields`): the event carries
the record's NEW field values (an append was resolved against the text then current when the
edit was recorded, so the original `.recorded` event is never rewritten), and the fold sets
exactly those fields. A field the catalogue does not list for the kind is an error, not a
silent drop: strict fold reports it, non-strict records it as a fold problem.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from .. import digest as D
from .. import record_fields as RF
from ..events import Event
from ..records import Bug, Decision, Lesson, Memory, ResearchNote, State
from .items import _h_updated
from .sessions import _session

_BOOKS = {
    "bug": ("bugs", Bug),
    "lesson": ("lessons", Lesson),
    "research": ("research", ResearchNote),
    "decision": ("decisions", Decision),
    "memory": ("memories", Memory),
}


def allowed(kind: str) -> set[str]:
    """The record fields an edit of ``kind`` may carry: the catalogue's, nothing else."""
    kd = RF.EVENT_KINDS[kind]
    return {*kd.attrs.values(), *(o.name for o in kd.own)}


def _handler(kind: str) -> Callable[[State, Event], None]:
    ok = allowed(kind)

    def handle(st: State, ev: Event) -> None:
        fields = ev.data.get("fields", {})
        if not isinstance(fields, dict):
            raise ValueError(f"{kind}.updated: fields must be an object")
        bad = sorted(set(fields) - ok)
        if bad:
            raise ValueError(f"{kind}.updated: unknown field(s) {', '.join(bad)}")
        if kind == "session":
            rec = _session(st, ev)
        else:
            book, cls = _BOOKS[kind]
            rec = getattr(st, book).setdefault(ev.subject, cls(id=ev.subject))
        for name, value in fields.items():
            setattr(rec, name, copy.deepcopy(value))

    return handle


#: The kinds a `record.corrected` event may name: the records held as events of their own kind
#: and the work items. Rules, schedules, triggers and data kinds are definitions with their own
#: revision path (`def.*`); a correction of one is refused, never silently dropped.
CORRECTABLE = (*RF.EVENT_KINDS, "task", "phase")
KIND = "record.corrected"


def _int_at_least_one(data: dict[str, Any], name: str, top: int | None = None) -> int:
    v = data.get(name)
    if isinstance(v, bool) or not isinstance(v, int) or v < 1 or (top is not None and v > top):
        bound = f" and at most {top}" if top else ""
        raise ValueError(f"{KIND}: {name} must be a whole number of at least 1{bound}; got {v!r}")
    return v


def _digest(value: Any, chars: int, min_chars: int) -> str:
    """A truncated digest of ``value``, or "" when the value is shorter than ``min_chars``:
    a short secret could be recovered by guessing against its digest."""
    text = D.canonical(value)
    return "" if len(text) < min_chars else D.content_digest(text, length=chars)


def _record_of(st: State, kind: str, rid: str) -> Any:
    if kind in ("task", "phase"):
        rec = st.items.get(rid)
        if rec is None or rec.removed or (rec.kind == "phase") != (kind == "phase"):
            return None
        return rec
    if kind == "session":
        return st.sessions.get(rid)
    book, _cls = _BOOKS[kind]
    return getattr(st, book).get(rid)


def _correct(st: State, ev: Event) -> None:
    """Replace fields of an existing record and keep an audit entry on it.

    The event carries the NEW values only; the fold sets them through the kind's own update
    handler and records who, why and a digest of the old and the new value of each field. The
    old value is never written anywhere by this event. Strict, like `<kind>.updated`."""
    d = ev.data
    kind, fields, reason = d.get("kind"), d.get("fields"), d.get("reason")
    if kind not in CORRECTABLE:
        raise ValueError(
            f"{KIND}: kind {kind!r} cannot be corrected; one of {', '.join(CORRECTABLE)}"
        )
    if not isinstance(fields, dict) or not fields:
        raise ValueError(f"{KIND}: fields must be a non-empty object")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError(f"{KIND}: a reason is required")
    chars = _int_at_least_one(d, "digest_chars", len(D.content_digest("")))
    min_chars = _int_at_least_one(d, "min_chars")
    # A digest of a low-entropy old value (a password, a short token) lets anyone holding the log
    # CONFIRM a guess, so a correction of a secret says `digest_old: false` and no digest of the
    # old value is kept at all; the default keeps one, as a check that the right text was replaced.
    digest_old = d.get("digest_old", True)
    if not isinstance(digest_old, bool):
        raise ValueError(f"{KIND}: digest_old must be true or false; got {digest_old!r}")
    kd = RF.KINDS[kind]
    ok = {*kd.attrs.values(), *(o.name for o in kd.own)}
    bad = sorted(set(fields) - ok)
    if bad:
        raise ValueError(f"{KIND}: unknown field(s) for a {kind}: {', '.join(bad)}")
    rec = _record_of(st, kind, ev.subject)
    if rec is None:
        raise ValueError(f"{KIND}: no {kind} {ev.subject}")
    rows = [
        {
            "field": name,
            "old": _digest(getattr(rec, name, ""), chars, min_chars) if digest_old else "",
            "new": _digest(value, chars, min_chars),
        }
        for name, value in sorted(fields.items())
    ]
    if kind in ("task", "phase"):
        _h_updated(
            st,
            Event(f"{kind}.updated", ev.subject, dict(fields), ev.agent, ev.lamport, ev.ts),
            kind,
        )
    else:
        UPDATE_HANDLERS[f"{kind}.updated"](
            st,
            Event(
                f"{kind}.updated", ev.subject, {"fields": dict(fields)}, ev.agent, ev.lamport, ev.ts
            ),
        )
    st.corrections.setdefault(f"{kind}:{ev.subject}", []).append(
        {"at": ev.ts, "by": ev.agent, "lamport": ev.lamport, "reason": reason, "fields": rows}
    )


UPDATE_HANDLERS: dict[str, Callable[[State, Event], None]] = {
    **{f"{kind}.updated": _handler(kind) for kind in RF.EVENT_KINDS},
    KIND: _correct,
}

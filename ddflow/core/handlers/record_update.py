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

from .. import record_fields as RF
from ..events import Event
from ..records import Bug, Decision, Lesson, Memory, ResearchNote, State
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


UPDATE_HANDLERS: dict[str, Callable[[State, Event], None]] = {
    f"{kind}.updated": _handler(kind) for kind in RF.EVENT_KINDS
}

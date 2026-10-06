"""Fold handlers: links between records, and the wrapper that records an add's links.

Each `_h_*` is a `(State, Event) -> None` fold handler, assembled into `model.HANDLERS`;
the other functions here are the helpers they share. All of it is pure."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..events import Event
from ..records import ADD_RELATIONS, RecordLinks, State


#: kind -> handler. The single declaration of the event vocabulary.
def _links(st: State, record: str) -> RecordLinks:
    return st.links.setdefault(record, RecordLinks(id=record))


def link_targets(v: Any) -> list[str]:
    """A link field is one id or a list of them; anything else names nothing."""
    if isinstance(v, str):
        return [v] if v else []
    if isinstance(v, list | tuple):
        return [x for x in v if isinstance(x, str) and x]
    return []


def _link(st: State, ev: Event, relation: str, target: str, source: str) -> None:
    eid = ev.id or ev.compute_id()
    d = ev.data
    _links(st, ev.subject).link_entries[f"{eid}:{relation}:{target}"] = {
        "event": eid,
        "relation": relation,
        "target": target,
        "by": d.get("by", "") or ev.agent,
        "at": ev.ts,
        "score": d.get("score"),
        "source": source,
        # A `ddflow link --reason` says WHY the pair was settled (`distinct` especially).
        # Carried into the projection, not only the raw event: `addenda`/`show`/`status`
        # spread this dict, and a field written to the event but dropped here is
        # unreadable from every API surface.
        "reason": d.get("reason", ""),
    }


def _linking(handler: Callable[[State, Event], None]) -> Callable[[State, Event], None]:
    """An ADD handler that also records the link fields its event carries."""

    def handle(st: State, ev: Event) -> None:
        handler(st, ev)
        d = ev.data
        for relation in ADD_RELATIONS:
            for target in link_targets(d.get(relation)):
                _link(st, ev, relation, target, "add")
        if isinstance(d.get("dedupe"), dict):
            _links(st, ev.subject).answers[ev.id or ev.compute_id()] = {
                **d["dedupe"],
                "at": ev.ts,
            }

    return handle


def _h_record_extended(st: State, ev: Event) -> None:
    """A verbatim addition to an existing record. Touches nothing the record already says."""
    d = ev.data
    eid = ev.id or ev.compute_id()
    _links(st, ev.subject).additions[eid] = {
        "event": eid,
        "text": d.get("text", ""),
        "who": d.get("who", "") or ev.agent,
        "at": ev.ts,
        "score": d.get("score"),
    }


def _h_link_recorded(st: State, ev: Event) -> None:
    """A link made after the record was added, or a 'distinct' dismissal."""
    d = ev.data
    # Recorded as given, even a relation this code does not know: a newer ddflow's new
    # relation or a typo must stay visible to a reader, not fold to "nothing happened".
    relation = d.get("relation", "") or "unspecified"
    for target in link_targets(d.get("target")):
        _link(st, ev, relation, target, "later")

"""Fold handlers: exported documents: enabled, disabled, acknowledged.

The `_h_*` functions are fold handlers (`(State, Event) -> None`; `_h_added`/`_updated`/
`_removed` also take the record kind) or handler factories (`_h_gate`, `_h_state`), assembled
into `model.HANDLERS`; the other functions are the helpers they share. All of it is pure."""

from __future__ import annotations

from ..events import Event
from ..records import State


def _h_export_enabled(st: State, ev: Event) -> None:
    d = ev.data
    human = bool(d.get("human"))
    st.exports[ev.subject] = {
        "enabled": True,
        "by": str(d.get("by") or ev.agent),
        "human": human,
        "at": ev.ts,
        "path": str(d.get("path", "")),
        "mode": str(d.get("mode", "")),
        "local": bool(d.get("local")),
        # Only a person at a terminal lifts the operator's veto. The writer enforces that
        # (an agent's enable of a locked document is refused); the fold ignores the event's
        # own `locked` claim and honours its `human` flag as it does for every event kind:
        # the log has no authenticity beyond what its writers record.
        "locked": bool(st.exports.get(ev.subject, {}).get("locked")) and not human,
        "acked": human,  # an agent's enable waits for the operator to acknowledge it
    }


def _h_export_disabled(st: State, ev: Event) -> None:
    d = ev.data
    prev = st.exports.get(ev.subject, {})
    st.exports[ev.subject] = {
        **prev,
        "enabled": False,
        "by": str(d.get("by") or ev.agent),
        "human": bool(d.get("human")),
        "at": ev.ts,
        "locked": bool(prev.get("locked")) or bool(d.get("locked")),
        "acked": True,  # stopping a document needs no acknowledgement
    }


def _h_export_acknowledged(st: State, ev: Event) -> None:
    if not ev.data.get("human"):  # only a person's acknowledgement clears the notice
        return
    docs = ev.data.get("documents")
    for doc in docs if isinstance(docs, list) else []:
        if isinstance(doc, str) and doc in st.exports:
            st.exports[doc]["acked"] = True

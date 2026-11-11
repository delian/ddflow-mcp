"""Fold handlers: exported documents: enabled, disabled, acknowledged.

`model.HANDLERS` maps each event kind to an `_h_*` function here -- directly, or through
the small wrapper or factory call it shows. Everything in this module is pure."""

from __future__ import annotations

from ..events import Event
from ..records import State


def _lock_of(prev: dict) -> dict | None:
    """The standing lock ``{by, human, at}`` of a document's fold entry, or None."""
    lock = prev.get("lock")
    return lock if prev.get("locked") and isinstance(lock, dict) else None


def _h_export_enabled(st: State, ev: Event) -> None:
    d = ev.data
    human = bool(d.get("human"))
    by = str(d.get("by") or ev.agent)
    lock = _lock_of(st.exports.get(ev.subject, {}))
    # A person lifts any lock; an agent lifts only its own agent lock (B-export-agent-lock).
    if lock and not (human or (not lock.get("human") and lock.get("by") == by)):
        keep = lock
    else:
        keep = None
    st.exports[ev.subject] = {
        "enabled": True,
        "by": by,
        "human": human,
        "at": ev.ts,
        "path": str(d.get("path", "")),
        "mode": str(d.get("mode", "")),
        "local": bool(d.get("local")),
        # Only a person at a terminal lifts the operator's veto. The writer enforces that
        # (an agent's enable of a locked document is refused); the fold ignores the event's
        # own `locked` claim and honours its `human` flag as it does for every event kind:
        # the log has no authenticity beyond what its writers record.
        "locked": keep is not None,
        "lock": keep,
        "acked": human,  # an agent's enable waits for the operator to acknowledge it
    }


def _h_export_disabled(st: State, ev: Event) -> None:
    d = ev.data
    prev = st.exports.get(ev.subject, {})
    human = bool(d.get("human"))
    lock = _lock_of(prev)
    if d.get("locked") and (lock is None or (human and not lock.get("human"))):
        # the first lock stands; a person's lock replaces an agent's, never the reverse
        lock = {"by": str(d.get("by") or ev.agent), "human": human, "at": ev.ts}
    st.exports[ev.subject] = {
        **prev,
        "enabled": False,
        "by": str(d.get("by") or ev.agent),
        "human": human,
        "at": ev.ts,
        "locked": lock is not None,
        "lock": lock,
        "acked": True,  # stopping a document needs no acknowledgement
    }


def _h_export_acknowledged(st: State, ev: Event) -> None:
    if not ev.data.get("human"):  # only a person's acknowledgement clears the notice
        return
    docs = ev.data.get("documents")
    for doc in docs if isinstance(docs, list) else []:
        if isinstance(doc, str) and doc in st.exports:
            st.exports[doc]["acked"] = True

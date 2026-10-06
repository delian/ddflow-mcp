"""Fold handlers: item definition and lifecycle: added, updated, removed, state changes, reopen, unblock.

Each `_h_*` is a `(State, Event) -> None` fold handler, assembled into `model.HANDLERS`;
the other functions here are the helpers they share. All of it is pure."""

from __future__ import annotations

from typing import Any

from ..events import Event, changelog_of
from ..records import ABANDONED, BLOCKED, DONE, OPEN, RUNNING, Item, State
from ._common import _item


def _safe_parent(st: State, item_id: str, parent: str) -> str:
    """``parent``, unless accepting it would make ``item_id`` its own ancestor.

    A self-parented item is its own open descendant, so it is permanently an umbrella:
    never offered, never claimable, never completable, and the only diagnosis is a
    blocked item that names itself. No CLI or MCP path can produce one today, but
    `fold` must survive a hand-written or future-version event — it is the one function
    in this package that is fed arbitrary JSON from disk and has no right to refuse it.
    """
    if not parent or parent == item_id:
        return ""
    seen = {item_id, parent}
    node = st.items.get(parent)
    while node is not None and node.parent:
        if node.parent == item_id:
            return ""
        if node.parent in seen:
            break
        seen.add(node.parent)
        node = st.items.get(node.parent)
    return parent


def _definition(ev: Event) -> dict[str, Any]:
    return {
        "event": ev.id or ev.compute_id(),
        "agent": ev.agent,
        "lamport": ev.lamport,
        "ts": ev.ts,
        "title": ev.data.get("title", ""),
        "body": ev.data.get("body", ""),
        "data": dict(ev.data),
    }


def _h_added(st: State, ev: Event, kind: str) -> None:
    """Define an item -- or, when a live item is already defined differently, contest it.

    A re-add of a REMOVED id is a new definition and ends any old contest. The same
    definition arriving twice (identical data) is not a contest: nothing would be lost.
    """
    prior = st.definitions.get(ev.subject)
    it = _item(st, ev, kind)
    mine = _definition(ev)
    if prior is not None and not it.removed and prior["data"] != mine["data"]:
        if not it.contested:
            it.contested = [prior]
        if all(d["data"] != mine["data"] for d in it.contested):
            it.contested.append(mine)
    elif it.removed:
        it.contested = []
    st.definitions[ev.subject] = mine
    _apply_definition(st, it, ev.data, kind)


def _apply_definition(st: State, it: Item, d: dict[str, Any], kind: str) -> None:
    it.kind = kind
    it.title = d.get("title", it.title)
    it.parent = _safe_parent(st, it.id, d.get("parent", it.parent))
    it.needs = list(d.get("needs", it.needs))
    it.globs = list(d.get("globs", it.globs))
    it.resources = list(d.get("resources", it.resources))
    it.body = d.get("body", it.body)
    it.tags = list(d.get("tags", it.tags))
    it.priority = int(d.get("priority", it.priority))
    it.source = d.get("source", it.source)
    it.fixes = list(d.get("fixes", it.fixes))
    for f in ("line", "port_of", "port_from", "port_strategy", "promote_from", "promote_to"):
        setattr(it, f, d.get(f, getattr(it, f)))
    it.removed = False


def _h_updated(st: State, ev: Event, kind: str) -> None:
    it = _item(st, ev, kind)
    d = ev.data
    for f in ("title", "parent", "body", "blocked_reason", "line"):
        if f in d:
            setattr(it, f, _safe_parent(st, it.id, d[f]) if f == "parent" else d[f])
    for f in ("needs", "globs", "tags", "resources"):
        if f in d:
            setattr(it, f, list(d[f]))
    if "priority" in d:
        it.priority = int(d["priority"])


def _h_removed(st: State, ev: Event, kind: str) -> None:
    _item(st, ev, kind).removed = True


def _h_state(new_state: str):
    def handler(st: State, ev: Event) -> None:
        it = _item(st, ev, ev.data.get("kind", "task"))
        it.state = new_state
        if new_state == RUNNING:
            it.blocked_reason = ""
            if it.lease is not None:
                it.lease.started = True
        elif new_state == BLOCKED:
            it.blocked_reason = ev.data.get("reason", "")
        elif new_state == DONE:
            it.completed_at = ev.ts
            # `or`, not a default: `complete` without --sha writes "sha": "", which used
            # to ERASE the sha `merge` recorded -- so no finished item was ever found on
            # any branch, and every version's item list and item-based bump were empty.
            it.merged_sha = ev.data.get("sha") or it.merged_sha
            # The importer has always written this -- `{"imported": True, "evidence":
            # "ticked in docs/todo.md:41"}` -- and the fold has always thrown it away,
            # so the one record of WHY an item was closed without running a single gate
            # existed only in the raw log. Third instance of this class in this series.
            it.completion_evidence = ev.data.get("evidence", it.completion_evidence)
            it.changelog = changelog_of(ev.data.get("changelog")) or it.changelog
        elif new_state == ABANDONED:
            it.blocked_reason = ev.data.get("reason", "")

    return handler


def _h_reopened(st: State, ev: Event) -> None:
    """A DONE item returns to OPEN because its completion did not hold (`ddflow verify
    --reopen`). Anything not done is left exactly as it is."""
    it = st.items.get(ev.subject)
    if it is None or it.state != DONE:
        return
    it.state = OPEN
    it.completed_at = ""
    it.lease = None
    it.gates = {}
    it.reopened.append(
        {
            "at": ev.ts,
            "by": ev.agent,
            "reason": ev.data.get("reason", ""),
            "claims": list(ev.data.get("claims") or []),
            "forced": bool(ev.data.get("forced")),
        }
    )


def _h_unblocked(st: State, ev: Event) -> None:
    """Release a BLOCKED item back to OPEN. Anything else is left exactly as it is.

    Without this there was no way back: a blocked item could only be forced past with
    `claim --force`, which also overrides dependencies and live leases. An import that
    lands a project's deferred work as BLOCKED needs the one-word inverse, or "held" is
    a synonym for "lost".
    """
    it = st.items.get(ev.subject)
    if it is None or it.state != BLOCKED:
        return
    it.state = OPEN
    it.blocked_reason = ""

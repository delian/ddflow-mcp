"""What changed on the roadmap (B-rm-changes, D-roadmap): changes since a point, links, an ETA.

Pure over the Query and the plain rows `kind_roadmap` builds. No clock: the point in time
is a date given, the date of the latest version tag, or nothing; the forecast's "now" is the
time of the last event in the log.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC
from typing import Any

from ...core import clock
from ...services.guidance.kinds import overlapping_live
from ...services.metrics.facts import collect
from ...services.metrics.forecast import forecast
from . import roadmap_calc as RC
from .query import Query

#: Entries listed per kind of change and per phase link list; the rest are counted.
LIST_CAP = 10
LINK_CAP = 5

_ADDED = ("phase.added", "task.added")
_PRIORITY = ("phase.updated", "task.updated")
_KINDS = (*_ADDED, *_PRIORITY, "item.completed", "item.blocked")


def latest_tag_date(q: Query) -> str:
    """The date (YYYY-MM-DD) of the newest version tag, or "" with no repo, no tag or no git."""
    if q.repo is None:
        return ""
    from ...config import Config
    from .kind_changelog import _tags

    try:
        tags = _tags(q.repo, Config.load(q.repo).flow.tag_prefix)
    except Exception:
        return ""
    return tags[-1].date if tags else ""


def resolve_since(q: Query, given: str) -> str:
    """The point the Changed section counts from: ``--since``, else the latest tag's date."""
    return given or latest_tag_date(q)


def _label(kind: str) -> str:
    if kind in _ADDED:
        return "added"
    return {"item.completed": "completed", "item.blocked": "blocked"}.get(kind, "reprioritised")


def changed(
    q: Query, since: str, visible: Callable[[str], bool], cap: int = LIST_CAP
) -> dict[str, Any]:
    """``{since, added, completed, blocked, reprioritised}``: each a ``{items, more}`` of
    ``{id, title, kind}`` (the last also ``priority``), the latest event per item, log order.
    Empty ``since`` gives no section (``{}``)."""
    if not since:
        return {}
    found: dict[str, dict[str, dict[str, Any]]] = {
        k: {} for k in ("added", "completed", "blocked", "reprioritised")
    }
    for e in q.events_of(*_KINDS, since=since):
        it = q.state.items.get(e.subject)
        if it is None or not visible(it.id):
            continue
        label = _label(e.kind)
        if label == "reprioritised" and "priority" not in e.data:
            continue
        row = {"id": it.id, "title": it.title, "kind": it.kind}
        if label == "reprioritised":
            row["priority"] = e.data["priority"]
        found[label][it.id] = row
    out: dict[str, Any] = {"since": since}
    for label, rows in found.items():
        listed = list(rows.values())
        out[label] = {"items": listed[:cap], "more": max(len(listed) - cap, 0)}
    return out


def links(q: Query, owned: dict[str, list[str]], cap: int = LINK_CAP) -> dict[str, dict[str, Any]]:
    """Per phase id: the live decisions whose globs overlap its (and its tasks') globs and
    the research notes about it or its tasks. ``owned`` maps a phase to the ids under it."""
    out: dict[str, dict[str, Any]] = {}
    notes: dict[str, list[str]] = {}
    for n in q.state.research.values():
        if n.item:
            notes.setdefault(n.item, []).append(n.id)
    for pid, ids in owned.items():
        globs = sorted({g for i in ids if i in q.state.items for g in q.state.items[i].globs})
        decisions = [d.id for d in overlapping_live(q.state, globs)] if globs else []
        research = sorted({r for i in ids for r in notes.get(i, [])})
        out[pid] = {
            "decisions": decisions[:cap],
            "more_decisions": max(len(decisions) - cap, 0),
            "research": research[:cap],
            "more_research": max(len(research) - cap, 0),
        }
    return out


def _now(q: Query) -> float:
    """The log's own present: the time of its last event (0 for an empty log)."""
    if not q.events:
        return 0.0
    try:
        return clock.parse_ts(q.events[-1].ts).replace(tzinfo=UTC).timestamp()
    except ValueError:
        return 0.0


def etas(q: Query, cfg: Any, phases: list[str]) -> dict[str, dict[str, Any]]:
    """Per phase id: ``{low_s, high_s}`` from the metrics forecast (an ESTIMATE), only for
    phases it has a range for; a phase it says 'not enough data' about is absent -- never a
    number made up."""
    out: dict[str, dict[str, Any]] = {}
    if not phases:
        return out
    facts = collect(q.events, q.state)
    now = _now(q)
    for pid in phases:
        f = forecast(facts, q.state, cfg, now, pid)
        if f["available"]:
            out[pid] = {"low_s": f["low_s"], "high_s": f["high_s"]}
    return out


_HOUR, _DAY = 3600, 86400


def hours(seconds: float) -> str:
    """``seconds`` as a short span: minutes under an hour, hours under two days, else days."""
    if seconds < _HOUR:
        return f"{max(round(seconds / 60), 1)} min"
    if seconds < 2 * _DAY:
        return f"{seconds / _HOUR:.1f} h"
    return f"{seconds / _DAY:.1f} days"


def eta_line(eta: dict[str, Any]) -> str:
    """The range as text, ``3.0 h to 1.2 days``; the template prefixes it ``ETA (estimate):``."""
    return f"{hours(eta['low_s'])} to {hours(eta['high_s'])}"


def visibility(state: Any, hide: list[str], phase: str = "") -> Callable[[str], bool]:
    """Is an item listed: it and its ancestors are not hidden (a named ``phase`` always is)."""

    def visible(item_id: str) -> bool:
        it = state.items.get(item_id)
        if it is None:
            return False
        return not any(
            a.id != phase and RC.hidden(a.id, a.tags, hide) for a in [it, *state.ancestors(it.id)]
        )

    return visible

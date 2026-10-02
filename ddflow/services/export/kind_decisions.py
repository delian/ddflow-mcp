"""The decisions index (``DECISIONS.md``): one table row per decision -- id, status, date,
title and what it governs. The full text stays in the log (``ddflow decision show``)."""

from __future__ import annotations

from typing import Any

from . import registry
from .frame import one_line
from .query import Query

GOVERNS_SHOWN = 3


def _status(d: Any) -> str:
    if d.superseded_by:
        return f"superseded by {d.superseded_by}"
    return d.status or "accepted"


def _cell(s: str) -> str:
    """A table cell: one line, no pipe that would end it."""
    return one_line(s, 120).replace("|", "\\|")


def data(q: Query, f: registry.Filters) -> dict[str, Any]:
    rows: list[dict[str, str]] = []
    for d in q.decisions():
        st = _status(d)
        if f.status and not st.startswith(f.status):
            continue
        if f.since and (d.at or "") < f.since:
            continue
        g = ", ".join(d.globs[:GOVERNS_SHOWN]) + (" ..." if len(d.globs) > GOVERNS_SHOWN else "")
        rows.append(
            {
                "id": d.id,
                "status": _cell(st),
                "date": (d.at or "")[:10],
                "title": _cell(d.title),
                "governs": _cell(g) if g else "-",
                "_k": d.at or "",
            }
        )
    rows.sort(key=lambda r: (r["_k"], r["id"]), reverse=True)  # newest first
    total = len(rows)
    live = sum(1 for r in rows if r["status"] == "accepted")  # of the whole set, not the page
    if f.limit:
        rows = rows[: f.limit]
    for r in rows:
        r.pop("_k")
    return {"rows": rows, "total": total, "shown": len(rows), "live": live}


registry.register(
    registry.DocKind(
        name="decisions",
        default_target="DECISIONS.md",
        data=data,
        update_mode=registry.WHOLE,
        filters=frozenset({"since", "limit", "status"}),
        title="Decisions index: id, status, date, title, governs",
    )
)

"""The work log: one line per item per day, with its merge, completion and bug-fixed
events coalesced (``LOG.md``).

Three data traps, each from reading real logs:

* An imported journal note is dated by ``data.at`` (when it was true), NOT by the event's
  ``ts`` (when it was imported): one import stamped 1,758 notes with one day. The date of
  such an entry is ``data.at`` with ``ts`` as the fallback.
* An item that was merged, completed and fixed a bug in one afternoon is ONE line naming
  the three, not three lines.
* A research line has no title of its own; it reads ``state.research`` for one.

Imported notes are an index, not a copy: a short first line and a pointer to
``data.source``. With no ``--since`` the window is the 30 days ending at the newest dated
entry in the log (never "now": the same log must give the same bytes).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from ...core import clock
from . import registry
from .frame import one_line
from .kind_changelog import _fixed_bugs
from .query import Query

#: Window used when no ``--since`` is given.
DEFAULT_WINDOW_DAYS = 30
#: Longest first line shown for an imported note; the rest stays in the note.
NOTE_CHARS = 160

_LABEL = {
    "item.completed": "completed",
    "worktree.merged": "merged",
    "bug.found": "bug found",
    "bug.fixed": "bug fixed",
    "bug.invalid": "bug invalid",
    "decision.recorded": "decision",
    "lesson.recorded": "lesson",
    "research.recorded": "research",
}
_KINDS = (*_LABEL, "session.note")


def _day(e: Any) -> str:
    """The date an event belongs to: ``data.at`` for a note that records something older
    than its own writing, else ``ts``."""
    at = str(e.data.get("at") or "") if e.kind == "session.note" else ""
    return (at or e.ts)[:10]


def _is_journal(e: Any) -> bool:
    """A note worth a work-log line: an imported one (it carries its origin). A live
    agent note belongs to its session, not here."""
    return e.kind == "session.note" and bool(e.data.get("source") or e.data.get("at"))


def _title(q: Query, kind: str, subject: str) -> str:
    st = q.state
    if kind in ("bug.found", "bug.fixed", "bug.invalid"):
        b = st.bugs.get(subject)
        return one_line(b.summary, 110) if b else ""
    if kind == "research.recorded":
        r = st.research.get(subject)
        return one_line(r.question or r.claim, 110) if r else ""
    if kind == "decision.recorded":
        d = st.decisions.get(subject)
        return one_line(d.title, 110) if d else ""
    if kind == "lesson.recorded":
        lsn = st.lessons.get(subject)
        return one_line(lsn.title, 110) if lsn else ""
    it = st.items.get(subject)
    return one_line(it.title, 110) if it else ""


def _window(q: Query, f: registry.Filters) -> str:
    """The first day shown. ``--since`` if given, else 30 days before the newest entry."""
    if f.since:
        q.events_of("worklog.noop", since=f.since)  # refuses an unreadable --since
        return f.since[:10]
    days = [_day(e) for e in q.events_of(*_KINDS) if e.kind != "session.note" or _is_journal(e)]
    if not days:
        return ""
    newest = max(days)
    try:
        return (clock.parse_date(newest) - timedelta(days=DEFAULT_WINDOW_DAYS)).isoformat()
    except ValueError:
        return ""


def data(q: Query, f: registry.Filters) -> dict[str, Any]:
    since = _window(q, f)
    # (day, subject) -> {"labels": {label: first ts}, "sort": (ts, id), ...}
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for e in q.events_of(*_KINDS):
        if e.kind == "session.note":
            if not _is_journal(e):
                continue
            day = _day(e)
            if since and day < since:
                continue
            gid = (day, "note", e.id or e.compute_id())
            src = str(e.data.get("source") or "")
            groups[gid] = {
                "labels": {"note": ""},
                "ts": "",
                "subject": "",
                "title": one_line(str(e.data.get("text") or ""), NOTE_CHARS),
                "source": src,
                "bugs": set(),
                "sort": ("", e.id or e.compute_id()),
            }
            continue
        day = _day(e)
        if since and day < since:
            continue
        gid = (day, "item", e.subject)
        g = groups.get(gid)
        if g is None:
            g = groups[gid] = {
                "labels": {},
                "ts": e.ts[11:16],
                "subject": e.subject,
                "title": _title(q, e.kind, e.subject),
                "source": "",
                "bugs": set(),
                "sort": (e.ts, e.subject),
            }
        g["labels"].setdefault(_LABEL[e.kind], e.ts)  # label -> first time seen
        g["sort"] = min(g["sort"], (e.ts, e.subject))
    _fold_bug_fixes(q, groups)
    days: dict[str, list[dict[str, Any]]] = {}
    for (day, _, _), g in groups.items():
        if g.get("dropped"):
            continue
        days.setdefault(day, []).append(g)
    out_days: list[dict[str, Any]] = []
    entries = 0
    for day in sorted(days, reverse=True):  # newest first
        rows = sorted(days[day], key=lambda g: g["sort"])
        lines = [_line(g) for g in rows]
        out_days.append({"date": day, "lines": lines})
        entries += len(lines)
    if f.limit:
        out_days, entries = _limit(out_days, f.limit)
    return {
        "since": since,
        "has_since": bool(since),
        "days": out_days,
        "entries": entries,
        "windowed": not f.since,
        "window_days": DEFAULT_WINDOW_DAYS,
    }


def _fold_bug_fixes(q: Query, groups: dict[tuple[str, str, str], dict[str, Any]]) -> None:
    """A ``bug.fixed`` whose bug is named by a task title ``(fixes bug X)`` that has its own
    line the same day moves onto that line. The title is the only link the log keeps
    between a fix task and its bug."""
    for (day, kind, subject), g in list(groups.items()):
        if kind != "item" or ("completed" not in g["labels"] and "merged" not in g["labels"]):
            continue
        it = q.item(subject)
        # The changelog's parser: "(fixes bugs A, B and X)" names A, B and X. Splitting
        # on commas alone read "B and X" as one id and left both bugs on their own lines
        # (Bf228d082de).
        for bug in _fixed_bugs(it.title) if it else []:
            other = groups.get((day, "item", bug))
            if other is not None and set(other["labels"]) <= {"bug fixed"}:
                g["labels"].setdefault("bug fixed", other["labels"]["bug fixed"])
                g["bugs"].add(bug)
                other["dropped"] = True


def _line(g: dict[str, Any]) -> dict[str, str]:
    """One finished markdown list line (the template only loops, so no inline tag can
    swallow a newline)."""
    # chronological: the order things happened, ties by name
    labels = ", ".join(sorted(g["labels"], key=lambda x: (g["labels"][x], x)))
    parts = [f"{g['ts']} " if g["ts"] else "", f"**{labels}**"]
    if g["subject"]:
        parts.append(f" `{g['subject']}`")
    if g["title"]:
        parts.append(f" {g['title']}")
    unnamed = [b for b in sorted(g["bugs"]) if b not in g["title"]]
    if unnamed:  # a title that already says "(fixes bug X)" need not say it twice
        parts.append(" (bug " + ", ".join(f"`{b}`" for b in unnamed) + ")")
    if g["source"]:
        parts.append(f" [source]({g['source']})")
    return {"text": "".join(parts)}


def _limit(days: list[dict[str, Any]], n: int) -> tuple[list[dict[str, Any]], int]:
    """Keep the newest ``n`` lines (days are newest first)."""
    kept: list[dict[str, Any]] = []
    left = n
    for d in days:
        if left <= 0:
            break
        take = d["lines"][-left:]  # lines inside a day run oldest to newest
        kept.append({"date": d["date"], "lines": take})
        left -= len(take)
    return kept, n - left


registry.register(
    registry.DocKind(
        name="worklog",
        default_target="LOG.md",
        data=data,
        update_mode=registry.WHOLE,
        filters=frozenset({"since", "limit"}),
        title="Work log: one line per item per day, newest first",
    )
)

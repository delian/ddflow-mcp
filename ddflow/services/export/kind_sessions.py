"""Sessions (``SESSION.md``): per session the agent, model and time window, the summary
FIRST (what a reader wants), then the operator's prompts, the notes, the items touched and
the handoff.

Two facts about this repository's own log shape the layout:

* Most ``session.prompt`` text here is a subagent brief written by the orchestrating
  agent, not words the operator typed. Such a prompt is labelled ``brief``; the rest is
  labelled ``operator``. The log cannot say which it was, so the label is a heuristic
  (see ``_is_brief``) and the document says so.
* The fold keeps a session's prompts and notes but not its ``session.ended`` summary, so
  the summary is read from the events (the last ``session.ended`` of the session wins).
  The summary is also the handoff: the one place an agent says what is left.
"""

from __future__ import annotations

from typing import Any

from . import registry
from .frame import one_line
from .query import Query

PROMPT_CHARS = 300
NOTE_CHARS = 240
SUMMARY_CHARS = 600
#: A prompt longer than this, or opening like a task assignment, reads as a brief.
BRIEF_MIN_CHARS = 1200
_BRIEF_OPENERS = ("you are ", "your agent name", "task:", "implement ", "fix ", "build ")


def _is_brief(text: str) -> bool:
    t = text.strip().lower()
    return len(t) >= BRIEF_MIN_CHARS or t.startswith(_BRIEF_OPENERS) or "subagent" in t[:300]


#: Events that mean "this agent worked on this item".
_WORK = ("item.started", "lease.acquired", "item.completed", "worktree.merged")


def _opened(s: Any) -> str:
    """When a session began: its start, else its first prompt, else unknown."""
    return s.started_at or (s.prompts[0]["at"] if s.prompts else "")


def _touched(q: Query) -> dict[str, set[str]]:
    """session id -> item ids named by its prompts and notes, plus the items its agent
    started, leased, merged or completed during the session's window. The log has no
    session id on those events, so the agent name and the time window are the link."""
    by_agent: dict[str, list[tuple[str, str]]] = {}
    for e in q.events_of(*_WORK):
        if e.agent and e.subject in q.state.items:
            by_agent.setdefault(e.agent, []).append((e.ts, e.subject))
    out: dict[str, set[str]] = {}
    for s in q.sessions():
        ids = {p.get("item", "") for p in s.prompts} | {n.get("item", "") for n in s.notes}
        lo = _opened(s)
        hi = s.ended_at or "9999"
        for ts, subject in by_agent.get(s.agent, ()):
            if ts >= lo and ts <= hi:
                ids.add(subject)
        out[s.id] = {i for i in ids if i}
    return out


def data(q: Query, f: registry.Filters) -> dict[str, Any]:
    summaries: dict[str, str] = {}
    for e in q.events_of("session.ended"):  # log order: the last one wins
        text = str(e.data.get("summary") or "").strip()
        if text:
            summaries[e.subject] = text
    touched = _touched(q)
    rows: list[dict[str, Any]] = []
    for s in q.sessions():
        if f.session and s.id != f.session:
            continue
        opened = _opened(s)
        if f.since and (s.ended_at or opened or "") < f.since:
            continue
        summary = summaries.get(s.id, "")
        prompts = [
            {
                "at": p["at"][:16].replace("T", " "),
                "who": "brief" if _is_brief(p["text"]) else "operator",
                "text": one_line(p["text"], PROMPT_CHARS),
            }
            for p in s.prompts
            if p["text"].strip()
        ]
        notes = [
            {"at": n["at"][:16].replace("T", " "), "text": one_line(n["text"], NOTE_CHARS)}
            for n in s.notes
            if n["text"].strip() and not (n.get("source") or n.get("origin_at"))
        ]
        imported = sum(1 for n in s.notes if n.get("source") or n.get("origin_at"))
        rows.append(
            {
                "id": s.id,
                "agent": s.agent,
                "model": s.model or "unrecorded",
                "opened": opened[:16].replace("T", " "),
                "closed": s.ended_at[:16].replace("T", " ") if s.ended_at else "open",
                "summary": one_line(summary, SUMMARY_CHARS) if summary else "",
                "has_summary": bool(summary),
                "prompts": prompts,
                "notes": notes,
                "imported_notes": imported,
                "summary_text": one_line(summary, SUMMARY_CHARS) if summary else "none recorded.",
                "items_text": ", ".join(f"`{i}`" for i in sorted(touched.get(s.id, ())))
                or "none recorded",
                "_sort": (opened, s.id),
            }
        )
    rows.sort(key=lambda r: r["_sort"], reverse=True)  # newest session first
    total = len(rows)
    if f.limit:
        rows = rows[: f.limit]
    for r in rows:
        r.pop("_sort")
    return {
        "sessions": rows,
        "total": total,
        "shown": len(rows),
        "with_summary": sum(1 for r in rows if r["has_summary"]),
    }


registry.register(
    registry.DocKind(
        name="sessions",
        default_target="SESSION.md",
        data=data,
        update_mode=registry.WHOLE,
        filters=frozenset({"since", "limit", "session"}),
        title="Sessions: summary first, then prompts, notes and items touched",
    )
)

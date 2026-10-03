"""Session list and detail, read straight from the event log (read-only).

Not the fold: the fold does not keep what a viewer needs (the implicit marker, which
copy of a prompt is an adopted orphan's) and a viewer must not depend on `export
sessions` being enabled. An adopted orphan is listed ONCE, as the copy under its
session, exactly as `sessions.replay` does; an orphan nobody adopted belongs to no
session and is not listed (`doctor` counts those, `session adopt-orphans` attaches them).

Every free-text field goes through the same redaction the export documents use.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Any

from ..config import Config
from .export.query import ExportError, _cutoff
from .export.safe import redact_text

DEFAULT_LIMIT = 50
MAX_LIMIT = 1000
_KINDS = ("session.started", "session.ended", "session.prompt", "session.note")
STATES = ("open", "ended")


class SessionViewError(ValueError):
    """The request cannot be answered as asked; the message says what to change."""


@dataclass
class _Acc:
    id: str
    agent: str = ""
    model: str = ""
    tool: str = ""
    implicit: bool = False
    started: str = ""
    ended: str = ""
    summary: str = ""
    last: str = ""
    entries: list[dict[str, Any]] = field(default_factory=list)


def _gather(events: list) -> dict[str, _Acc]:
    adopted = {(e.data or {})["adopted_from"] for e in events if (e.data or {}).get("adopted_from")}
    out: dict[str, _Acc] = {}
    for ev in events:
        if ev.kind not in _KINDS or not (ev.subject or "").strip() or ev.id in adopted:
            continue
        s = out.setdefault(ev.subject, _Acc(id=ev.subject, agent=ev.agent))
        s.last = max(s.last, ev.ts)
        d = ev.data or {}
        if ev.kind == "session.started":
            s.started = s.started or ev.ts
            s.agent = ev.agent
            s.model = d.get("model", "") or s.model
            s.tool = d.get("tool", "") or s.tool
            s.implicit = s.implicit or bool(d.get("implicit"))
        elif ev.kind == "session.ended":
            s.ended = ev.ts
            s.summary = d.get("summary", "") or s.summary
        else:
            s.entries.append(
                {
                    "kind": "prompt" if ev.kind == "session.prompt" else "note",
                    "at": d.get("orphan_at") or ev.ts,
                    "text": d.get("text", ""),
                    "item": d.get("item", ""),
                }
            )
    for s in out.values():
        s.entries.sort(key=lambda e: e["at"])  # stable: equal times keep log order
        s.started = s.started or (s.entries[0]["at"] if s.entries else s.last)
    return out


def _items(s: _Acc) -> list[str]:
    return sorted({e["item"] for e in s.entries if e["item"]})


def _row(s: _Acc, cfg: Config) -> dict[str, Any]:
    return {
        "id": s.id,
        "agent": s.agent,
        "state": "ended" if s.ended else "open",
        "implicit": s.implicit,
        "started": s.started,
        "ended": s.ended,
        "last": s.ended or s.last,
        "prompts": sum(1 for e in s.entries if e["kind"] == "prompt"),
        "notes": sum(1 for e in s.entries if e["kind"] == "note"),
        "items": len(_items(s)),
        "model": redact_text(s.model, cfg).text,
    }


@dataclass
class SessionList:
    rows: list[dict[str, Any]]
    total: int
    limit: int
    filters: dict[str, str]

    @property
    def truncated(self) -> bool:
        return self.total > len(self.rows)


def list_sessions(
    events: list,
    cfg: Config,
    *,
    state: str = "",
    agent: str = "",
    since: str = "",
    limit: int = DEFAULT_LIMIT,
) -> SessionList:
    """Sessions newest activity first (ties by id), at most `limit`."""
    if state and state not in STATES:
        raise SessionViewError(f"state {state!r}: one of {', '.join(STATES)}")
    if limit < 1:
        raise SessionViewError(f"limit must be at least 1, got {limit}")
    limit = min(limit, MAX_LIMIT)
    try:
        at_or_after = _cutoff(since)
    except ExportError as exc:
        raise SessionViewError(str(exc).replace("--since", "since")) from None
    rows = [_row(s, cfg) for s in _gather(events).values()]
    if state:
        rows = [r for r in rows if r["state"] == state]
    if agent:
        rows = [r for r in rows if r["agent"] == agent]
    if at_or_after is not None:
        rows = [r for r in rows if r["last"] and at_or_after(r["last"])]
    rows.sort(key=lambda r: r["id"])
    rows.sort(key=lambda r: r["last"], reverse=True)
    given = {k: v for k, v in {"state": state, "agent": agent, "since": since}.items() if v}
    return SessionList(rows=rows[:limit], total=len(rows), limit=limit, filters=given)


def show_session(events: list, cfg: Config, session_id: str) -> dict[str, Any]:
    """One session with every prompt and note in order; unknown id -> near matches."""
    sessions = _gather(events)
    s = sessions.get(session_id)
    if s is None:
        near = difflib.get_close_matches(session_id, sorted(sessions), n=5, cutoff=0.6)
        near += sorted(i for i in sessions if session_id and session_id in i and i not in near)[:5]
        hint = f" -- did you mean: {', '.join(near)}?" if near else ""
        raise SessionViewError(
            f"no session {session_id!r}{hint}"
            + ("" if near else " (`ddflow session list` shows them)")
        )
    d = _row(s, cfg)
    d.update(
        tool=redact_text(s.tool, cfg).text,
        summary=redact_text(s.summary, cfg).text,
        items=[redact_text(i, cfg).text for i in _items(s)],
        entries=[
            {
                "kind": e["kind"],
                "at": e["at"],
                "item": redact_text(e["item"], cfg).text,
                "text": redact_text(e["text"], cfg).text,
            }
            for e in s.entries
        ],
    )
    return d

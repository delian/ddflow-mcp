"""The shared list engine behind the task, phase, bug, research and session viewers.

Read-only and pure over a folded `State`: no disk, no new events. A viewer is a
projection of what the fold already knows, so there is nothing here to keep in step
with the writers -- only a decision per kind about which field is "the state", "the
owner" and "the last change", made ONCE below.

Every row is one line of facts (`id kind state title owner updated`); a caller wanting
the whole record uses `show <id>`. Free text that reaches a row (titles, summaries, the
first words of a session prompt) goes through the same redaction the export documents
use, so a listing cannot carry a secret the log itself holds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..config import Config
from ..core.model import Bug, Item, ResearchNote, Session, State
from .export.safe import redact_text

KINDS = ("task", "phase", "bug", "research", "session")
DEFAULT_LIMIT = 50
MAX_LIMIT = 1000
_TITLE_MAX = 120


class ViewError(ValueError):
    """The request cannot be answered as asked; the message says what to change."""


@dataclass
class View:
    kind: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    #: Rows that matched the filters, before `limit` cut the list.
    total: int = 0
    limit: int = DEFAULT_LIMIT
    filters: dict[str, str] = field(default_factory=dict)

    @property
    def truncated(self) -> bool:
        return self.total > len(self.rows)


#: Which filters each kind can honour. A filter a kind has no data for is REFUSED, not
#: ignored: `bug list --agent x` returning every bug would read as "x filed all of
#: these", which is a claim the log does not make.
_FILTERS: dict[str, frozenset[str]] = {
    "task": frozenset({"state", "phase", "tag", "agent", "since"}),
    "phase": frozenset({"state", "tag", "agent", "since"}),
    "bug": frozenset({"state", "phase", "since"}),
    "research": frozenset({"state", "phase", "tag", "since"}),
    "session": frozenset({"state", "agent", "since"}),
}


def _one_line(text: str, cfg: Config) -> str:
    first = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    clean = redact_text(first, cfg).text
    return clean if len(clean) <= _TITLE_MAX else clean[: _TITLE_MAX - 1] + "…"


def _item_row(it: Item, cfg: Config) -> dict[str, Any]:
    updated = it.completed_at or it.created_at
    if it.lease is not None:
        renewed = datetime.fromtimestamp(it.lease.renewed_at, tz=UTC).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )
        updated = max(updated, renewed)
    return {
        "id": it.id,
        "kind": it.kind,
        "state": it.state,
        "title": _one_line(it.title, cfg),
        "owner": it.lease.holder if it.lease else "",
        "updated": updated,
        "phase": it.parent,
        "tags": list(it.tags),
    }


def _bug_row(b: Bug, cfg: Config, st: State) -> dict[str, Any]:
    it = st.items.get(b.item)
    return {
        "id": b.id,
        "kind": "bug",
        "state": b.resolution or "open",
        "title": _one_line(b.title or b.summary, cfg),
        "owner": "",
        "updated": b.fixed_at or b.invalid_at or b.found_at,
        "phase": (it.parent or it.id) if it else "",
        "tags": [],
    }


def _research_row(r: ResearchNote, cfg: Config, st: State) -> dict[str, Any]:
    it = st.items.get(r.item)
    return {
        "id": r.id,
        "kind": "research",
        "state": (r.verdict or "unverdicted").lower(),
        "title": _one_line(r.question or r.claim, cfg),
        "owner": "",
        "updated": r.at,
        "phase": (it.parent or it.id) if it else "",
        "tags": list(r.tags),
    }


def _session_row(s: Session, cfg: Config) -> dict[str, Any]:
    first = s.prompts[0].get("text", "") if s.prompts else ""
    return {
        "id": s.id,
        "kind": "session",
        "state": "ended" if s.ended_at else "open",
        "title": _one_line(first or s.model, cfg),
        "owner": s.agent,
        "updated": s.ended_at or s.started_at,
        "phase": "",
        "tags": [],
    }


def _candidates(st: State, kind: str, cfg: Config) -> list[dict[str, Any]]:
    if kind == "task":
        return [_item_row(i, cfg) for i in st.items.values() if i.kind == "task" and not i.removed]
    if kind == "phase":
        return [_item_row(i, cfg) for i in st.phases()]
    if kind == "bug":
        return [_bug_row(b, cfg, st) for b in st.bugs.values()]
    if kind == "research":
        return [_research_row(r, cfg, st) for r in st.research.values()]
    return [_session_row(s, cfg) for s in st.sessions.values()]


def _in_phase(st: State, row: dict[str, Any], phase: str) -> bool:
    if row["kind"] == "task":
        return row["id"] in {t.id for t in st.tasks(phase)}
    return row["phase"] == phase


def list_view(
    st: State,
    cfg: Config,
    kind: str,
    *,
    state: str = "",
    phase: str = "",
    tag: str = "",
    agent: str = "",
    since: str = "",
    limit: int = DEFAULT_LIMIT,
) -> View:
    """Rows of `kind`, newest change first (ties by id), at most `limit` of them."""
    if kind not in KINDS:
        raise ViewError(f"unknown kind {kind!r}: one of {', '.join(KINDS)}")
    asked = {"state": state, "phase": phase, "tag": tag, "agent": agent, "since": since}
    given = {k: v for k, v in asked.items() if v}
    unsupported = sorted(k for k in given if k not in _FILTERS[kind])
    if unsupported:
        raise ViewError(
            f"{kind} has no {', '.join(unsupported)} to filter by "
            f"(it supports: {', '.join(sorted(_FILTERS[kind]))})"
        )
    if limit < 1:
        raise ViewError(f"limit must be at least 1, got {limit}")
    limit = min(limit, MAX_LIMIT)
    if phase and not (phase in st.items and st.items[phase].kind == "phase"):
        raise ViewError(f"no phase {phase!r}")

    rows = _candidates(st, kind, cfg)
    if state:
        rows = [r for r in rows if r["state"] == state.lower()]
    if phase:
        rows = [r for r in rows if _in_phase(st, r, phase)]
    if tag:
        rows = [r for r in rows if tag in r["tags"]]
    if agent:
        rows = [r for r in rows if r["owner"] == agent]
    if since:
        rows = [r for r in rows if r["updated"] and r["updated"] >= since]
    # Two stable sorts: id ascending, then newest first, so equal timestamps keep id order.
    rows.sort(key=lambda r: r["id"])
    rows.sort(key=lambda r: r["updated"], reverse=True)
    return View(kind=kind, rows=rows[:limit], total=len(rows), limit=limit, filters=given)

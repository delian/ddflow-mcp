"""The shared list engine behind the task, phase, bug, research and session viewers.

Read-only and pure over a folded `State`: no disk, no new events. A viewer is a
projection of what the fold already knows, so there is nothing here to keep in step
with the writers -- only a decision per kind about which field is "the state", "the
owner" and "the last change", made ONCE below.

Every row is one line of facts (`id kind state title owner updated`); a caller wanting
the whole record uses `show <id>`. The free text that reaches a row (the title, taken
from a title, summary, question or the first words of a session prompt, and the tags)
goes through the same redaction the export documents use. `owner` is an agent id and
`state` a fixed word, neither free text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..config import Config
from ..core import clock
from ..core import progress as PR
from ..core.model import Bug, Item, Lesson, ResearchNote, Session, State
from .export.query import ExportError, _cutoff
from .export.safe import redact_text

KINDS = ("task", "phase", "bug", "research", "lesson", "session")
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
    # `item`: the queue item a bug was filed in or affects -- the bug's own `item` field,
    # not its phase (a bug filed against no item has none).
    "bug": frozenset({"state", "phase", "since", "item"}),
    "research": frozenset({"state", "phase", "tag", "since"}),
    # `owner` is the lesson's `by`: the agent that first recorded it.
    "lesson": frozenset({"state", "tag", "agent", "since"}),
    "session": frozenset({"state", "agent", "since"}),
}


def _one_line(text: str, cfg: Config) -> str:
    first = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    clean = redact_text(first, cfg).text
    return clean if len(clean) <= _TITLE_MAX else clean[: _TITLE_MAX - 1] + "…"


def _phase_of(st: State, it: Item | None) -> str:
    """The phase enclosing `it`, however deeply nested: itself for a phase, the nearest
    phase ancestor for a task or sub-task, "" for none."""
    return PR.phase_of(st, it.id) if it is not None else ""


def _tags(tags: list[str], cfg: Config) -> list[str]:
    return [redact_text(t, cfg).text for t in tags]


def _item_row(st: State, it: Item, cfg: Config) -> dict[str, Any]:
    updated = it.completed_at or it.created_at
    if it.lease is not None:
        renewed = clock.iso_at(it.lease.renewed_at)
        updated = max(updated, renewed)
    return {
        "id": it.id,
        "kind": it.kind,
        "state": it.state,
        "title": _one_line(it.title, cfg),
        "owner": it.lease.holder if it.lease else "",
        "updated": updated,
        "phase": _phase_of(st, it),
        "tags": _tags(it.tags, cfg),
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
        "phase": _phase_of(st, it),
        "tags": [],
        # The queue item the bug is against (`""` when it names none): `--item` filters on
        # this, and it is the bug's OWN field -- `phase` is derived from the same item, but
        # a bug can name an item whose phase differs from where it was filed.
        "item": b.item,
        # The two columns a reader scanning a bug list needs: what guards the fix
        # (B227585c781 records the tests one by one) and the lesson the close recorded.
        # `show <id>` has the whole record; these are the facts that belong in a row.
        "regression_tests": list(b.regression_tests),
        "lesson": b.lesson,
    }


def _lesson_row(ls: Lesson, cfg: Config) -> dict[str, Any]:
    return {
        "id": ls.id,
        "kind": "lesson",
        # Two states in one word each: a lesson is still believed, or replaced.
        "state": "superseded" if ls.superseded_by else "live",
        "title": _one_line(ls.title or ls.rule, cfg),
        "owner": ls.by,
        "updated": ls.at,
        "phase": "",
        "tags": _tags(ls.tags, cfg),
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
        "phase": _phase_of(st, it),
        "tags": _tags(r.tags, cfg),
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
        return [
            _item_row(st, i, cfg) for i in st.items.values() if i.kind == "task" and not i.removed
        ]
    if kind == "phase":
        return [_item_row(st, i, cfg) for i in st.phases()]
    if kind == "bug":
        return [_bug_row(b, cfg, st) for b in st.bugs.values()]
    if kind == "research":
        return [_research_row(r, cfg, st) for r in st.research.values()]
    if kind == "lesson":
        return [_lesson_row(ls, cfg) for ls in st.lessons.values()]
    return [_session_row(s, cfg) for s in st.sessions.values()]


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
    item: str = "",
    limit: int = DEFAULT_LIMIT,
) -> View:
    """Rows of `kind`, newest change first (ties by id), at most `limit` of them."""
    if kind not in KINDS:
        raise ViewError(f"unknown kind {kind!r}: one of {', '.join(KINDS)}")
    asked = {
        "state": state,
        "phase": phase,
        "tag": tag,
        "agent": agent,
        "since": since,
        "item": item,
    }
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
        rows = [r for r in rows if r["state"].lower() == state.lower()]
    if phase:
        rows = [r for r in rows if r["phase"] == phase]
    if tag:
        want_tag = redact_text(tag, cfg).text  # compared as the row shows it
        rows = [r for r in rows if want_tag in r["tags"]]
    if agent:
        rows = [r for r in rows if r["owner"] == agent]
    if item:
        # Only kinds that carry an `item` may be filtered by one (`_FILTERS`); `.get` keeps
        # this honest if a future kind with the filter lacks the field.
        rows = [r for r in rows if r.get("item") == item]
    if since:
        try:
            at_or_after = _cutoff(since)
        except ExportError as exc:
            raise ViewError(str(exc).replace("--since", "since")) from None
        rows = [r for r in rows if r["updated"] and at_or_after(r["updated"])]
    # Two stable sorts: id ascending, then newest first, so equal timestamps keep id order.
    rows.sort(key=lambda r: r["id"])
    rows.sort(key=lambda r: r["updated"], reverse=True)
    return View(kind=kind, rows=rows[:limit], total=len(rows), limit=limit, filters=given)

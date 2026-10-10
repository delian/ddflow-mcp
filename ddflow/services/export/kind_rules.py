"""The ``rules`` document kind: what binds work here, from the log and the config.

Three parts (decision D-export, 7): the decisions in force (accepted or proposed, never
superseded; a proposed one is marked) grouped by the path each governs, the live lessons grouped by tag as one-line summaries, and the enforced workflow
(the same data ``ddflow workflow`` reports: the gates, and the ``[gates]``/``[schedule]``/
``[enforce]`` rules). Imported rulebooks (AGENTS.md and the like) are never copied in.

Lessons are summaries, never full text, and ``--limit`` / ``--tag`` bound them (the lessons only: decisions are grouped by path): a project
with hundreds of lessons would otherwise produce a document nobody reads (168 KB for 473).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from ...config import Config
from ..gates import load_gates
from ..workflow import describe
from . import registry
from .frame import one_line
from .query import ExportError, Query, _parse_ts

UNTAGGED = "(untagged)"
#: Per D-export (7) a proposed decision is listed (and marked); any other status is not.
IN_FORCE = ("accepted", "proposed")
TITLE_CHARS = 150
TEXT_CHARS = 220


def _when(at: str) -> datetime:
    """A lesson's timestamp as an instant, so "newest" does not rest on one text form."""
    try:
        return _parse_ts(at)
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)


def _first_paragraph(text: str) -> str:
    for para in (text or "").split("\n\n"):
        if para.strip():
            return para
    return ""


def _decisions(q: Query) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(decisions grouped by governing glob, decisions with no glob), both stably ordered."""
    by_glob: dict[str, list[dict[str, Any]]] = {}
    loose: list[dict[str, Any]] = []
    for d in q.decisions():  # id order
        if d.status not in IN_FORCE or d.superseded_by:
            continue
        row = {
            "id": d.id,
            "title": one_line(d.title, TITLE_CHARS),
            "status": d.status,
            "decided_by": d.decided_by,
            "decision": one_line(d.decision, TEXT_CHARS),
        }
        globs = sorted({g for g in d.globs if g})
        if not globs:
            loose.append(row)
        for g in globs:
            by_glob.setdefault(g, []).append(row)
    groups = [{"path": g, "decisions": by_glob[g]} for g in sorted(by_glob)]
    return groups, loose


def _lessons(q: Query, f: registry.Filters) -> tuple[list[dict[str, Any]], int, int]:
    """(groups by tag, lessons shown, lessons left out by --limit).

    A lesson is listed once, under its first tag (alphabetical; the filtered tag when
    --tag is given), so a heavily tagged corpus does not multiply. --limit keeps the
    NEWEST N of the live, matching lessons.
    """
    live = [lz for lz in q.lessons() if not lz.superseded_by and (not f.tag or f.tag in lz.tags)]
    live.sort(key=lambda lz: (_when(lz.at), lz.id), reverse=True)
    omitted = 0
    if f.limit and len(live) > f.limit:
        omitted = len(live) - f.limit
        live = live[: f.limit]
    groups: dict[str, list[dict[str, Any]]] = {}
    for lz in sorted(live, key=lambda lz: lz.id):
        tag = f.tag or (sorted(lz.tags)[0] if lz.tags else UNTAGGED)
        text = lz.summary or _first_paragraph(lz.rule)
        groups.setdefault(tag, []).append(
            {
                "id": lz.id,
                "title": one_line(lz.title, 100),
                "text": one_line(text, TEXT_CHARS),
            }
        )
    out = [{"tag": t, "lessons": groups[t]} for t in sorted(groups)]
    return out, len(live), omitted


def _value(v: Any) -> str:
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v) or "(none)"
    return str(v)


def workflow_data(q: Query) -> dict[str, Any] | None:
    """The enforced workflow, from the config in force; ``None`` for a Query with no repo
    (built from events in memory). Anything that stops it being read is ``ExportError``:
    a rules document with the workflow silently missing would read as "no workflow"."""
    if q.repo is None:
        return None

    try:
        cfg = Config.load(q.repo)
        view = describe(q.repo, cfg, load_gates(q.repo, cfg), state=q.state)
    except Exception as exc:
        raise ExportError(f"could not read the workflow: {type(exc).__name__}: {exc}") from exc
    gates = []
    for g in view.gates:
        if not g.in_task:
            continue
        marks = [
            m
            for m, on in (
                ("required", g.required),
                ("evidence required", g.evidence),
                ("different-family reviewer", g.reviewer == "different_family"),
            )
            if on
        ]
        gates.append(
            {
                "position": g.position,
                "id": g.id,
                "kind": g.kind,
                "marks": ", ".join(marks),
                "run": one_line(g.command or g.prompt, TEXT_CHARS),
                "is_command": bool(g.command),
            }
        )
    gates.sort(key=lambda g: (g["position"], g["id"]))
    rules = [{"key": k, "value": _value(v)} for k, (v, _src) in sorted(view.rules.items())]
    return {"gates": gates, "phase_pipeline": list(view.phase_pipeline), "rules": rules}


def data(q: Query, f: registry.Filters) -> Mapping[str, Any]:
    groups, loose = _decisions(q)
    lessons, shown, omitted = _lessons(q, f)
    return {
        "decision_groups": groups,
        "decisions_without_path": loose,
        "decision_count": len(
            {d["id"] for g in groups for d in g["decisions"]} | {d["id"] for d in loose}
        ),
        "lesson_groups": lessons,
        "lessons_shown": shown,
        "lessons_omitted": omitted,
        "tag": f.tag,
        "workflow": workflow_data(q),
    }


registry.register(
    registry.DocKind(
        name="rules",
        default_target="RULES.md",
        data=data,
        filters=frozenset({"limit", "tag"}),
        title="Decisions in force, lessons by tag and the enforced workflow",
    )
)

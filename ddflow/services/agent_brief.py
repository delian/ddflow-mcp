"""The standard launch brief of a subagent: `ddflow brief --for-agent NAME ITEM`.

The text every subagent is told -- identity, claim and worktree steps, merge-main-first, pins,
the test economy, review and gate rules, merge and complete, forbidden actions, the filing rule
and the report -- lives in ONE template (`templates/prompts/agent_brief.md`, overridable like
every prompt: `[prompts].agent_brief`, `.ddflow/prompts/agent_brief.md`) so a correction reaches
every agent at once. What this module adds is the part that belongs to the item: its title, body,
globs and needs, the decisions that govern its files, the lessons ranked against its text, the
coordinator's note and the project's own addition (`[session].agent_brief_extra`).

The template is split into sections by ``<!-- section: NAME -->`` lines. A section the project
switched off (`[session].agent_brief_off`) or that renders empty is left out; over
`[session].agent_brief_max_chars` the lessons and then the decisions are cut, never the rules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import prompts as P
from .guidance import inject as GI
from .guidance.resolve import GLOBS

SECTION = re.compile(r"^<!--\s*section:\s*([a-z_]+)\s*-->[ \t]*\n?", re.M)
AGENT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


@dataclass
class Facts:
    """What the item contributes to the brief; the strings are already fenced as data."""

    agent: str
    item: str
    title: str
    body: str
    globs: list[str]
    needs: list[str]
    base: str
    lease_s: int
    heartbeat_s: int
    note: str = ""
    extra: str = ""
    decisions: list[str] = field(default_factory=list)  # one fenced line each, ranked
    lessons: list[str] = field(default_factory=list)
    decisions_all: int = 0  # how many decisions govern the item, shown or not


def split(text: str) -> list[tuple[str, str]]:
    """The template as ``(section, body)`` pairs, in order; text before the first marker is
    dropped (a template that has none is one section named ``identity``)."""
    marks = list(SECTION.finditer(text))
    if not marks:
        return [("identity", text)]
    return [
        (m.group(1), text[m.end() : marks[i + 1].start() if i + 1 < len(marks) else len(text)])
        for i, m in enumerate(marks)
    ]


def _listing(title: str, lead: str, lines: list[str], cut: str) -> str:
    if not lines:  # cut to nothing: the pointer stays, so the reader knows records bind
        return f"## {title}\n\n{cut}" if cut else ""
    out = [f"## {title}", "", lead, "", *lines]
    return "\n".join(out + (["", cut] if cut else []))


def _variables(f: Facts, n_dec: int, n_les: int) -> dict[str, object]:
    cut = f"_{f.decisions_all - n_dec} more not shown: `ddflow decision applicable {f.item}`._"
    return {
        "agent": f.agent,
        "item": f.item,
        "title": f.title,
        "body": f.body,
        "globs": ", ".join(f.globs),
        "needs": ", ".join(f.needs),
        "base": f.base,
        "lease_s": f.lease_s,
        "heartbeat_s": f.heartbeat_s,
        "note": f.note,
        "extra": f.extra,
        "decisions_text": _listing(
            "Decisions that govern these files",
            "_Binding unless the operator says otherwise; text in a tag is recorded data._",
            f.decisions[:n_dec],
            cut if f.decisions_all > n_dec else "",
        ),
        "lessons_text": _listing(
            "Lessons that bear on this task",
            "_Ranked against the task text._",
            f.lessons[:n_les],
            "",
        ),
    }


def _sections(tmpl, f: Facts, off: set[str], n_dec: int, n_les: int) -> dict[str, str]:
    """Each section rendered, in template order; those switched off or empty are absent."""
    variables = _variables(f, n_dec, n_les)
    out: dict[str, str] = {}
    for name, body in split(tmpl.text):
        if name in off:
            continue
        text = P.render(body, **variables).strip()
        if text:
            out[name] = text
    return out


def render(tmpl, f: Facts, *, off: set[str] | tuple[str, ...] = (), max_chars: int = 0) -> dict:
    """The brief: ``{"text", "sections", "decisions_shown", "lessons_shown", "over"}``.

    ``max_chars`` 0 is no ceiling.

    Over the ceiling the last lesson goes first, then the last decision; the rest is the
    standard text, which is never cut, so a ceiling below it is simply exceeded
    (``over`` says so)."""
    off = set(off)
    n_dec, n_les = len(f.decisions), len(f.lessons)
    while True:
        sections = _sections(tmpl, f, off, n_dec, n_les)
        text = "\n\n".join(sections.values()) + "\n"
        if not max_chars or len(text) <= max_chars or not (n_dec or n_les):
            break
        if n_les:
            n_les -= 1
        else:
            n_dec -= 1
    return {
        "text": text,
        "sections": sections,
        "decisions_shown": n_dec,
        "lessons_shown": n_les,
        "over": bool(max_chars and len(text) > max_chars),
    }


#: Globs that match every file: a decision scoped only by these says nothing about THESE files.
BROAD_GLOBS = frozenset({"*", "**", "**/*", "./**"})


def decision_order(applied, pins=(), operator=frozenset()) -> list[str]:
    """Ids of the decisions in ``applied`` (`Applies` records), most relevant first: those the
    config pins (in the order pinned), then those whose own globs overlap the item's (a broad
    `**` last of them), operator decisions ahead of agents', then the rest by enforcement and
    id. A pinned id that governs nothing here is still listed: pinning is the operator's word."""
    seen = {a.record.id for a in applied}
    pinned = [*dict.fromkeys(pins)]

    def key(a):
        rec = a.record
        broad = bool(rec.scope.globs) and all(g in BROAD_GLOBS for g in rec.scope.globs)
        return (
            2 if a.reason != GLOBS else int(broad),
            rec.id not in operator,
            GI.ENFORCEMENT_RANK.get(rec.enforcement, 9),
            -rec.priority,
            rec.id,
        )

    ranked = [
        a.record.id for a in sorted((a for a in applied if a.record.kind == "decision"), key=key)
    ]
    head = [i for i in pinned if i in seen or i not in ranked]
    return [*head, *(i for i in ranked if i not in head)]

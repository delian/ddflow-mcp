"""The pure logic of the self-assessment pass: find the defects an assessment text names.

The pass itself (`api.knowledge.self_assessment.auto_file`) wraps this with the filing and
the duplicate check; this module only reads text into a list of named defects -- no events,
no ids, no git -- so it stays testable on its own and imports nothing above the `services`
layer (the import-linter layers contract). A defect named here, in the order named, is what
`auto_file` turns into one bug (or task) each, skipping one that reads like an existing
record.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: A defect is one line of the assessment: a bullet, or a numbered item. The rule's
#: ``(2) WHAT ... list it`` is exactly a list, so a list line is the unit the pass files.
_BULLET = re.compile(r"^\s*(?:[-*+]|\d{1,3}[.)])\s+(.+)$")

#: A line that says the answer is "none". A whole assessment that found nothing is not a
#: defect, and must not be filed as one.
_NONE_LINES = frozenset(
    {
        "none",
        "none found",
        "no bugs",
        "no defects",
        "no issues",
        "nothing",
        "nothing found",
    }
)

#: How much of a line becomes the bug title (the whole line stays the summary).
_TITLE_MAX = 120


@dataclass(frozen=True)
class Defect:
    """One defect an assessment text names, in the order named."""

    title: str
    summary: str
    severity: str = "medium"


def extract_defects(text: str | None, *, max_defects: int = 20) -> list[Defect]:
    """The defects `text` names, in order: each bullet or numbered line is one defect, and
    nothing else is. A line that says the answer is "none" names no defect, so an empty
    assessment is exactly `[]`."""
    if not text:
        return []
    out: list[Defect] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.lower() in _NONE_LINES:
            continue
        match = _BULLET.match(raw)
        body = match.group(1).strip() if match else line
        if not body:
            continue
        title = " ".join(body.split())[:_TITLE_MAX]
        out.append(Defect(title=title, summary=body))
        if len(out) >= max_defects:
            break
    return out


def report_line(text: str | None, *, filed: list[str], skipped: list[str]) -> str:
    """The one `ddflow assessment` line an assessment report ends with (rule (5)): the ids
    filed, the ones skipped as already tracked, or ``none found``. `text` is the assessment
    text itself: when it names no defect the line reads ``none`` rather than an empty list."""
    if not extract_defects(text):
        return "none found"
    parts: list[str] = []
    if filed:
        parts.append("filed: " + ", ".join(filed))
    if skipped:
        parts.append("skipped (already tracked): " + ", ".join(skipped))
    return ", ".join(parts) or "none found"

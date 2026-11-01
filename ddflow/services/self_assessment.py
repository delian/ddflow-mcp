"""The pure logic of the self-assessment pass: find the defects an assessment text names.

The pass itself (`api.knowledge.self_assessment.auto_file`) wraps this with the filing and
the duplicate check; this module only reads text into a list of named defects -- no events,
no ids, no git -- so it stays testable on its own and imports nothing above the `services`
layer (the import-linter layers contract). A defect named here, in the order named, is what
`auto_file` turns into one bug (or task) each, skipping one that reads like an existing
record.

Only a LIST line names a defect. The rule's ``(2) WHAT ... list it`` is a list, so a bullet
or numbered item is the unit the pass files; a prose heading or a sentence with no marker
is not a defect and is never filed (a whole assessment that found nothing must not become
a bug that says it did).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: A defect is one list line of the assessment: a bullet, or a numbered item.
_BULLET = re.compile(r"^\s*(?:[-*+]|\d{1,3}[.)])\s+(.+)$")

#: A phrase that says the answer is "none". A whole assessment that found nothing is not a
#: defect, and must not be filed as one.
_NONE_LINES = (
    "none",
    "none found",
    "no bugs",
    "no defects",
    "no issues",
    "nothing",
    "nothing found",
)

#: How much of a line becomes the bug title (the whole line stays the summary).
_TITLE_MAX = 120


@dataclass(frozen=True)
class Defect:
    """One defect an assessment text names, in the order named."""

    title: str
    summary: str
    severity: str = "medium"


def _none_phrase(word: str) -> bool:
    """Whether `word` -- a list line's text -- says the answer is "none". Trailing
    punctuation is not a different answer: ``None found.`` and ``none`` are the same."""
    return word.lower().strip().rstrip(".!;:, ").lower() in _NONE_LINES


def extract_defects(text: str | None, *, max_defects: int = 20) -> list[Defect]:
    """The defects `text` names, in order: only a bullet or numbered line names a defect,
    and nothing else does. A line that says the answer is "none" names no defect, so an
    empty assessment is exactly `[]`."""
    if not text:
        return []
    out: list[Defect] = []
    for raw in text.splitlines():
        match = _BULLET.match(raw)
        if match is None:
            continue  # prose is not a defect (a heading next to the list is filed as nothing)
        body = match.group(1).strip()
        if not body or _none_phrase(body):
            continue
        title = " ".join(body.split())[:_TITLE_MAX]
        out.append(Defect(title=title, summary=body))
        if len(out) >= max_defects:
            break
    return out


def report_line(
    text: str | None, *, filed: list[str], skipped: list[str], failed: list[str]
) -> str:
    """The one `ddflow assessment` line an assessment report ends with (rule (5)): the ids
    filed, the ones skipped as already tracked, the ones that failed to file, or
    ``none found``."""
    if not extract_defects(text):
        return "none found"
    parts: list[str] = []
    if filed:
        parts.append("filed: " + ", ".join(filed))
    if skipped:
        parts.append("skipped (already tracked): " + ", ".join(skipped))
    if failed:
        parts.append("failed to file: " + ", ".join(failed))
    return ", ".join(parts) or "none found"

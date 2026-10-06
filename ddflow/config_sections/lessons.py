"""The `[lessons]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import _doc


@dataclass
class LessonsConfig:
    """Searchable, self-compressing lessons store."""

    search_backend: str = "fts5"  # fts5 | like
    max_results: int = 5
    snippet_chars: int = 320
    cadence_growth_pct: float = 20.0
    cadence_min_entries: int = 25
    auto_capture_on_bug: bool = True
    require_regression_test: bool = True
    reflect_after_items: int = 3


_doc(
    "lessons",
    "reflect_after_items",
    "How many items may finish with NO lesson ever recorded before ddflow says so. One task finishing without a lesson is normal and reporting it would be noise; several in a row is the pattern the rule exists for. 0 disables the check.",
)
_doc(
    "lessons",
    "search_backend",
    "'fts5' uses SQLite full-text BM25 ranking; 'like' is a portable fallback for a SQLite built without FTS5. Detected automatically at init; this knob forces one.",
)
_doc(
    "lessons",
    "max_results",
    "Default number of lessons returned by a search and injected into a session brief. The whole point is to spend ~600 tokens instead of reading a 1.5 MB file.",
)
_doc(
    "lessons",
    "snippet_chars",
    "Characters of each lesson shown in search results and briefs before truncation to the full-entry pointer.",
)
_doc(
    "lessons",
    "cadence_growth_pct",
    "Percent growth in bytes-per-entry AND total bytes since the last compression pass that triggers a new one. Relative, not absolute, so a pass that just ran makes the trigger go quiet.",
)
_doc(
    "lessons",
    "cadence_min_entries",
    "Below this many entries the cadence never fires — compressing a small corpus costs more than it saves.",
)
_doc(
    "lessons",
    "auto_capture_on_bug",
    "Record a lesson automatically whenever a bug is closed, pre-filled from the bug record, so capture is the default rather than an act of virtue.",
)
_doc(
    "lessons",
    "require_regression_test",
    "Refuse to close a bug without a named regression test. This is the rule that stops the same bug shipping twice.",
)

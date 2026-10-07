"""The `[lessons]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, knob


@declare("lessons")
@dataclass
class LessonsConfig:
    """Searchable, self-compressing lessons store."""

    #: fts5 | like
    search_backend: str = knob(
        "fts5",
        doc="'fts5' uses SQLite full-text BM25 ranking; 'like' is a portable fallback for a SQLite built without FTS5. Detected automatically at init; this knob forces one.",
        choices=("fts5", "like"),
        strictest=("like", "no safety dimension; works on every SQLite build"),
    )
    max_results: int = knob(
        5,
        doc="Default number of lessons returned by a search and injected into a session brief. The whole point is to spend ~600 tokens instead of reading a 1.5 MB file.",
    )
    snippet_chars: int = knob(
        320,
        doc="Characters of each lesson shown in search results and briefs before truncation to the full-entry pointer.",
    )
    cadence_growth_pct: float = knob(
        20.0,
        doc="Percent growth in bytes-per-entry AND total bytes since the last compression pass that triggers a new one. Relative, not absolute, so a pass that just ran makes the trigger go quiet.",
    )
    cadence_min_entries: int = knob(
        25,
        doc="Below this many entries the cadence never fires — compressing a small corpus costs more than it saves.",
    )
    auto_capture_on_bug: bool = knob(
        True,
        doc="Record a lesson automatically whenever a bug is closed, pre-filled from the bug record, so capture is the default rather than an act of virtue.",
    )
    require_regression_test: bool = knob(
        True,
        doc="Refuse to close a bug without a named regression test. This is the rule that stops the same bug shipping twice.",
    )
    reflect_after_items: int = knob(
        3,
        doc="How many items may finish with NO lesson ever recorded before ddflow says so. One task finishing without a lesson is normal and reporting it would be noise; several in a row is the pattern the rule exists for. 0 disables the check.",
    )

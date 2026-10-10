"""The count-based due passes and the lessons-compression and export cadence rules.

Derived from the log like every other cadence: the trigger is how much the lessons
corpus has grown since the last recorded compression, so there is no state file to
drift out of step with reality.

In `services/` rather than `surfaces/` because `api/operations.py` computes the due list
and may not import a surface to finish it. It was briefly put in `surfaces/` during the
B36 extraction and `test_a_module_imports_only_from_below` refused it on the spot, which
is the layer check doing exactly what it is for.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Config
from ..core.model import State
from .export import ops
from .schedule import (
    calendar,
    calendar_due,
    count_at_last_run,
    count_passes,
)


def count_due(st, cfg, *, replaced: set[str] = frozenset()) -> list[dict[str, Any]]:
    """Passes due by COMPLETED WORK (tasks or phases). Needs nothing but the folded state
    and the config, so `complete <phase>` can ask it even when a calendar knob is malformed.
    `replaced` names calendar entries, which take the place of the count-based pass."""
    due: list[dict[str, Any]] = []
    for p in count_passes(st, cfg):
        if p.name in replaced:
            # The calendar entry of the same name REPLACES this pass; without the skip
            # it would also fall due by completions, reported twice under one name.
            continue
        since = p.count - count_at_last_run(st, p.name)
        if p.every > 0 and since >= p.every:
            due.append({"cadence": p.name, "since": since, "every": p.every, "unit": p.unit})
    return due


def phase_overdue(st, cfg) -> list[str]:
    """Blockers for completing a phase: every phase-counted pass that is due. A calendar entry
    replaces a count-based pass only while every entry is well-formed; one malformed entry
    leaves all count-based passes in force."""
    try:
        # The ONE parse of every_days (`calendar`): a second copy here accepted inf as a
        # period, so a pass it "replaced" stopped blocking the phase (roborev, B1c68fe5e9c).
        replaced = set(calendar(cfg))
    except ValueError:
        # `ddflow cadence` refuses a malformed list outright, so no calendar pass
        # can fire in place of a count-based one: replace nothing.
        replaced = set()
    return [
        f"periodic pass overdue: {d['cadence']} ({d['since']} of {d['every']} phases since "
        f"the last). Run it, then `ddflow cadence --ran {d['cadence']}`; to skip it on the "
        f'record: `ddflow cadence --ran {d["cadence"]} --note "skipped: <reason>"`.'
        for d in count_due(st, cfg, replaced=replaced)
        if d["unit"] == "phases"
    ]


def lessons_cadence(st, cfg) -> list[dict[str, Any]]:
    """Is a lessons-compression pass due?

    Measured as GROWTH since the last recorded pass, not as an absolute size. An
    absolute threshold fires forever once crossed -- including immediately after a pass
    that just correctly compressed the corpus -- which trains everyone to ignore it.
    Growth goes quiet when the work is done, which is the only behaviour that keeps a
    cadence trigger credible.

    Both halves must clear: total bytes AND bytes-per-entry. Dividing by entry count
    alone would fire on a MERGE (fewer entries, same bytes => per-entry rises), i.e. on
    exactly the action the cadence exists to produce.
    """
    live = [x for x in st.lessons.values() if not x.superseded_by]
    if len(live) < cfg.lessons.cadence_min_entries:
        return []
    runs = st.cadences.get("lessons_compression", [])
    now_bytes = sum(len(x.text().encode("utf-8")) for x in live)
    now_per = now_bytes / max(1, len(live))
    if not runs:
        return [
            {
                "cadence": "lessons_compression",
                "since": len(live),
                "every": 0,
                "unit": "entries (no baseline recorded yet)",
            }
        ]
    try:
        was = json.loads(runs[-1].get("result") or "{}")
        was_bytes, was_entries = float(was["bytes"]), int(was["entries"])
    except (ValueError, KeyError, TypeError):
        return []
    was_per = was_bytes / max(1, was_entries)
    grow_bytes = (now_bytes - was_bytes) / max(1.0, was_bytes) * 100
    grow_per = (now_per - was_per) / max(1e-9, was_per) * 100
    thresh = cfg.lessons.cadence_growth_pct
    if grow_bytes >= thresh and grow_per >= thresh:
        return [
            {
                "cadence": "lessons_compression",
                "since": round(grow_per, 1),
                "every": thresh,
                "unit": f"% growth per entry (total +{grow_bytes:.1f}%)",
            }
        ]
    return []


def export_cadence(repo, cfg) -> list[dict[str, Any]]:
    """Is an export refresh due? Only for a project that opted in: a selected document with
    ``refresh`` other than ``off`` whose target is stale (its content would change).

    A hand-edited or missing target is not "due" (a refresh would skip or create it, and the
    operator decides that); nothing is read, and nothing written, when no document opted in.
    Run the pass with ``ddflow export <doc> --update`` (the refresh triggers do it themselves).
    """

    try:
        docs = [
            s
            for s in (ops.spec_for(cfg, d, writing=True) for d in ops.selection(cfg))
            if s.refresh != "off" and s.mode == "whole"
        ]
        if not docs:
            return []
        q = ops.load(repo, cfg)
        stale = [s.doc for s in docs if ops.state_of(repo, cfg, q, s)[0] == "stale"]
    except Exception as exc:  # never fails the listing -- but "could not look" is not "not due"
        return [
            {
                "cadence": "export_refresh",
                "since": f"could not check ({type(exc).__name__}: {exc})",
                "every": 0,
                "unit": "export settings or event log unreadable; `ddflow export --all --check`",
            }
        ]
    if not stale:
        return []
    return [
        {
            "cadence": "export_refresh",
            "since": ", ".join(stale),
            "every": 0,
            "unit": "stale selected document(s); `ddflow export <doc> --update`",
        }
    ]


# -- the due evaluators ----------------------------------------------------------------------


@dataclass(frozen=True)
class DueContext:
    """What a due evaluator reads: the project, its config, the folded log and the calendar
    entries (`schedule.calendar`: name -> days) parsed once for all of them."""

    repo: Path
    cfg: Config
    st: State
    calendar: Mapping[str, float]
    #: The time the calendar entries are judged at (epoch seconds); None is the current time.
    now: float | None = None


#: A due evaluator: the passes that are due now, as `{cadence, since, every, unit}` rows.
Evaluator = Callable[[DueContext], list[dict[str, Any]]]
_EVALUATORS: dict[str, Evaluator] = {}


def register_due(name: str, evaluate: Evaluator) -> Evaluator:
    """Add a due evaluator under ``name``. `due_all` runs them in registration order, so a
    cadence that falls due by its own rule registers here instead of being called by hand
    from `ddflow cadence`. Refused (ValueError) for a name already registered."""
    if name in _EVALUATORS:
        raise ValueError(f"a due evaluator named {name!r} is already registered")
    _EVALUATORS[name] = evaluate
    return evaluate


def evaluators() -> dict[str, Evaluator]:
    """The registered due evaluators, in the order `due_all` runs them."""
    return dict(_EVALUATORS)


def due_all(ctx: DueContext) -> list[dict[str, Any]]:
    """Every periodic pass that is due now: each registered evaluator's rows, in order."""
    due: list[dict[str, Any]] = []
    for evaluate in _EVALUATORS.values():
        due += evaluate(ctx)
    return due


# The built-ins, in the order `ddflow cadence` has always listed them: the count-based passes
# (a calendar entry of the same name replaces one), lessons growth, stale exports, then the
# calendar entries.
register_due("count", lambda c: count_due(c.st, c.cfg, replaced=set(c.calendar)))
register_due("lessons", lambda c: lessons_cadence(c.st, c.cfg))
register_due("export", lambda c: export_cadence(c.repo, c.cfg))
register_due("calendar", lambda c: calendar_due(c.st, c.calendar, c.now))

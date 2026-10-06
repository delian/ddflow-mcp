"""Signals the event log itself shows, for the adaptive flow controller.

Pure functions of (events, the fold, config, ``now``): no I/O, no clock of their own, no
host probing. Every baseline is the project's OWN history from its log, so no number is
specific to a machine or a repository. A signal with too little history is ``None``,
which the controller (``flowcontrol``) treats as neutral: it can never justify raising
the limit and never lowers it.

Windows (all ending at ``now``):

* ``reviewer_latency_ratio``: median seconds from ``gate.started`` to the review gate's
  outcome over the last 30 minutes, divided by the project's median over the 7 days
  before that window. None under 5 recent or 20 baseline samples.
* ``gate_failure_rate``: ``gate.failed / (passed + failed)`` over the last 60 minutes.
  None under 10 outcomes.
* ``gate_failure_ratio``: that rate divided by the project's own rate over the 7 days
  before the window, so its marks mean "N times the usual" (D-unify 8: shrink at 2x).
  None under 10 recent or 20 baseline outcomes; a baseline with no failure counts as one,
  the smallest rate it could have measured.
* ``merge_failure_rate``: failed merge-gate outcomes over merge attempts in the last
  2 hours. None with no attempt.
* ``loop_findings``: how many findings the loops detector reports now.
* ``independent_ready``: how many ready items are mutually independent and independent
  of everything in flight, from ``schedule.plan`` itself with the caps lifted -- the same
  selection the offer makes, not a copy of it.
"""

from __future__ import annotations

import copy
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

from ..config import Config
from . import progress as PR
from .events import Event
from .model import State

RECENT_REVIEW_S = 30 * 60.0
BASELINE_S = 7 * 86400.0
MIN_RECENT_REVIEWS = 5
MIN_BASELINE_REVIEWS = 20
GATE_WINDOW_S = 60 * 60.0
MIN_GATE_OUTCOMES = 10
MIN_BASELINE_GATE_OUTCOMES = 20
MERGE_WINDOW_S = 120 * 60.0
MERGE_GATE = "merge"

_OUTCOME_KINDS = ("passed", "failed", "unavailable", "partial")


@dataclass(frozen=True)
class Signals:
    reviewer_latency_ratio: float | None = None
    gate_failure_rate: float | None = None
    merge_failure_rate: float | None = None
    loop_findings: int = 0
    independent_ready: int = 0
    gate_failure_ratio: float | None = None

    def as_signals(self) -> dict[str, float | None]:
        """The mapping a ``flowcontrol.Sample`` carries."""
        return {
            "reviewer_latency_ratio": self.reviewer_latency_ratio,
            "gate_failure_rate": self.gate_failure_rate,
            "gate_failure_ratio": self.gate_failure_ratio,
            "merge_failure_rate": self.merge_failure_rate,
            "loop_findings": float(self.loop_findings),
            "independent_ready": float(self.independent_ready),
        }


def _gate(ev: Event) -> str:
    g = ev.data.get("gate") if isinstance(ev.data, dict) else ""
    return g if isinstance(g, str) else ""


def _outcome(ev: Event) -> str:
    head, _, tail = ev.kind.partition(".")
    return tail if head == "gate" and tail in _OUTCOME_KINDS else ""


def reviewer_latency_ratio(events: Sequence[Event], now: float) -> float | None:
    started: dict[tuple[str, str], float] = {}
    recent: list[float] = []
    baseline: list[float] = []
    for ev in sorted(events, key=lambda e: (PR.epoch(e.ts), e.lamport)):
        gate = _gate(ev)
        if gate not in PR.REVIEW_GATES:
            continue
        at = PR.epoch(ev.ts)
        if at <= 0:
            continue
        key = (ev.subject, gate)
        if ev.kind == "gate.started":
            started[key] = at
        elif _outcome(ev) and key in started:
            took = at - started.pop(key)
            if took < 0 or at > now:
                continue
            if at > now - RECENT_REVIEW_S:
                recent.append(took)
            elif at > now - RECENT_REVIEW_S - BASELINE_S:
                baseline.append(took)
    if len(recent) < MIN_RECENT_REVIEWS or len(baseline) < MIN_BASELINE_REVIEWS:
        return None
    base = statistics.median(baseline)
    if base <= 0:
        return None
    return statistics.median(recent) / base


def _failure_rate(
    events: Sequence[Event], now: float, window: float, gate: str | None
) -> tuple[int, int]:
    """(passed, failed) outcomes, of ``gate`` or of every gate, in ``(now - window, now]``."""
    passed = failed = 0
    for ev in events:
        out = _outcome(ev)
        if out not in ("passed", "failed"):
            continue
        if gate is not None and _gate(ev) != gate:
            continue
        at = PR.epoch(ev.ts)
        if at <= 0 or at > now or at <= now - window:
            continue
        if out == "failed":
            failed += 1
        else:
            passed += 1
    return passed, failed


def gate_failure_rate(events: Sequence[Event], now: float) -> float | None:
    passed, failed = _failure_rate(events, now, GATE_WINDOW_S, None)
    total = passed + failed
    return failed / total if total >= MIN_GATE_OUTCOMES else None


def gate_failure_ratio(events: Sequence[Event], now: float) -> float | None:
    """The last hour's gate failure rate over the project's rate in the 7 days before it."""
    passed, failed = _failure_rate(events, now, GATE_WINDOW_S, None)
    if passed + failed < MIN_GATE_OUTCOMES:
        return None
    start = now - GATE_WINDOW_S  # the baseline ends where the recent window begins
    b_passed, b_failed = _failure_rate(events, start, BASELINE_S, None)
    if b_passed + b_failed < MIN_BASELINE_GATE_OUTCOMES:
        return None
    # A clean baseline counts as one failure, the smallest rate it could have measured:
    # a ratio to zero says nothing, and a first burst of failures must still register.
    return (failed / (passed + failed)) / (max(b_failed, 1) / (b_passed + b_failed))


def merge_failure_rate(events: Sequence[Event], now: float) -> float | None:
    passed, failed = _failure_rate(events, now, MERGE_WINDOW_S, MERGE_GATE)
    total = passed + failed
    return failed / total if total else None


def independent_ready(state: State, cfg: Config, now: float) -> int:
    """Ready items that overlap neither anything in flight nor each other. Asks
    ``schedule.plan`` with the parallelism caps lifted, so the count is what the offer
    would make of the same state given unlimited slots; the caller's config is untouched."""
    from .schedule import plan

    wide = copy.deepcopy(cfg)
    wide.schedule.max_parallel_tasks = 10**6
    wide.worktree.max_parallel = 10**6
    return len(plan(state, wide, now=now).ready)


def compute(events: Sequence[Event], state: State, cfg: Config, now: float) -> Signals:
    evs = list(events)
    return Signals(
        reviewer_latency_ratio=reviewer_latency_ratio(evs, now),
        gate_failure_rate=gate_failure_rate(evs, now),
        merge_failure_rate=merge_failure_rate(evs, now),
        loop_findings=len(PR.detect(evs, state, cfg)),
        independent_ready=independent_ready(state, cfg, now),
        gate_failure_ratio=gate_failure_ratio(evs, now),
    )


def history_notes(signals: Signals) -> list[str]:
    """Neutral one-line notes for ``doctor``: which log-derived signals have too little
    history yet. Informational, never a failure: such a signal is simply not used."""
    notes = []
    for name in ("reviewer_latency_ratio", "gate_failure_rate", "merge_failure_rate"):
        if getattr(signals, name) is None:
            notes.append(f"{name}: too little history in the log yet (neutral, not used)")
    # gate_failure_ratio shares gate_failure_rate's recent hour (noted above when short);
    # with that hour present, a None ratio means the 7-day baseline is what is short.
    if signals.gate_failure_ratio is None and signals.gate_failure_rate is not None:
        notes.append(
            f"gate_failure_ratio: under {MIN_BASELINE_GATE_OUTCOMES} gate outcomes in the "
            "7-day baseline yet (neutral, not used)"
        )
    return notes

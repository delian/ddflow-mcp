"""Signals the event log itself shows, for the adaptive flow controller.

Pure functions of (events, the fold, config, ``now``): no I/O, no clock of their own, no
host probing. Every baseline is the project's OWN history from its log, so no number is
specific to a machine or a repository. A signal with too little history is ``None``,
which the controller (``flowcontrol``) treats as neutral: it can never justify raising
the limit and never lowers it.

Windows (all ending at ``now``):

* ``reviewer_latency_ratio``: how long the REVIEWER takes to answer, now against usual
  (bug B1c5dbe3103): each sample is the ``elapsed_s`` a ``ddflow review`` records in its
  outcome's evidence, per wave of chunks it had to send -- so a large diff, a triage gap
  before a hand-recorded outcome or a killed run cannot pose as a slow reviewer. The
  median over the last 30 minutes is divided by the median over the 7 days before. It is
  saturation only while the reviewer is busy: None when the recent reviews ran with
  fewer than 3 in flight at once (one agent's rubber_duck and critic are two), and under
  5 recent or 20 baseline samples.
* ``gate_failure_rate``: ``gate.failed / (passed + failed)`` over the last 60 minutes.
  None under 10 outcomes.
* ``gate_failure_ratio``: that rate divided by the project's own rate over the 7 days
  before the window, so its marks mean "N times the usual" (D-unify 8: shrink at 2x).
  None under 10 recent or 20 baseline outcomes. A clean baseline counts as one failure; a
  single recent failure counts as at most the usual rate (1.0), since one is not a burst.
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
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from ..config import Config
from . import progress as PR
from .events import Event
from .model import State

RECENT_REVIEW_S = 30 * 60.0
BASELINE_S = 7 * 86400.0
MIN_RECENT_REVIEWS = 5
#: Reviews in flight at once (counting itself) below which slow answers are the diff's or
#: the model's, not a queue: one agent's rubber_duck and critic run as a pair.
MIN_IN_FLIGHT = 3
#: For a review recorded before its evidence carried `waves`: the chunks one wave holds
#: at the defaults. `services.review` sends every chunk's FIRST copy before any hedge
#: copy, up to `AUTO_CONCURRENCY_CEILING` (32) requests, so 32 chunks' answers arrive in
#: one round. A recorded `waves` (the reviewer's own concurrency) always wins.
WAVE_CHUNKS = 32
MIN_BASELINE_REVIEWS = 20
GATE_WINDOW_S = 60 * 60.0
MIN_GATE_OUTCOMES = 10
MIN_BASELINE_GATE_OUTCOMES = 20
MIN_RECENT_FAILURES = 2
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


def _review_samples(events: Sequence[Event], now: float) -> list[tuple[float, float, float]]:
    """(start, end, seconds per wave) of every review `ddflow review` recorded: an outcome
    of a review gate whose evidence carries the command's own ``elapsed_s``. A later
    `gate record` that carries the same review's evidence along is not a second sample.
    Nothing that ended after ``now`` (a skewed clock) is a sample or counts as in flight."""
    out = []
    seen: set[tuple] = set()
    for ev in sorted(events, key=lambda e: (PR.epoch(e.ts), e.lamport)):
        ev_data = ev.data if isinstance(ev.data, dict) else {}
        evidence = ev_data.get("evidence")
        if _gate(ev) not in PR.REVIEW_GATES or not _outcome(ev) or not isinstance(evidence, dict):
            continue
        took, chunks = evidence.get("elapsed_s"), evidence.get("chunks_total") or 1
        end = PR.epoch(ev.ts)
        if not isinstance(took, int | float) or isinstance(took, bool) or took <= 0:
            continue
        if end <= 0 or end > now:
            continue
        # the review's own identity, which a `gate record` copies along with its evidence
        same = (
            evidence.get("output_file")
            or evidence.get("output_digest")
            or (evidence.get("reviewed_head"), evidence.get("diff_sha"))
        )
        if same == (None, None):
            same = ev.lamport
        once = (ev.subject, _gate(ev), same, took)
        if once in seen:
            continue
        seen.add(once)
        waves = evidence.get("waves")  # recorded since bug B1c5dbe3103; estimated before
        if not isinstance(waves, int) or isinstance(waves, bool) or waves < 1:
            waves = max(1, -(-int(chunks) // WAVE_CHUNKS)) if isinstance(chunks, int) else 1
        out.append((end - took, end, took / waves))
    return out


def reviewer_latency_ratio(events: Sequence[Event], now: float) -> float | None:
    samples = sorted(_review_samples(events, now))
    recent = [s for s in samples if now - RECENT_REVIEW_S < s[1] <= now]
    base = [
        s for s in samples if now - RECENT_REVIEW_S - BASELINE_S < s[1] <= now - RECENT_REVIEW_S
    ]
    if len(recent) < MIN_RECENT_REVIEWS or len(base) < MIN_BASELINE_REVIEWS:
        return None
    # how many reviews were running when each recent one started (itself included)
    busy = [sum(1 for a, b, _ in samples if a <= start < b) for start, _, _ in recent]
    if statistics.median(busy) < MIN_IN_FLIGHT:
        return None
    usual = statistics.median(s[2] for s in base)
    if usual <= 0:
        return None
    return statistics.median(s[2] for s in recent) / usual


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
    # The measured baseline, as it is; a clean one counts as one failure, the smallest rate
    # it could have shown, so a burst after a clean week still registers.
    ratio = (failed / (passed + failed)) / (max(b_failed, 1) / (b_passed + b_failed))
    # One failure is not a burst: against a clean busy week it would read as 100x and
    # shrink the limit for a whole hour, so a lone failure counts as the usual rate at most.
    return ratio if failed >= MIN_RECENT_FAILURES else min(ratio, 1.0)


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


def history_notes(signals: Signals, enabled: Collection[str] | None = None) -> list[str]:
    """Neutral one-line notes for ``doctor``: which log-derived signals have too little
    history yet. Informational, never a failure: such a signal is simply not used.
    ``enabled`` (default: all) limits them to the signals the controller reads."""

    def shown(name: str) -> bool:
        return enabled is None or name in enabled

    notes = []
    for name in ("reviewer_latency_ratio", "gate_failure_rate", "merge_failure_rate"):
        if shown(name) and getattr(signals, name) is None:
            # the latency ratio is also None while too few reviews run at once to be busy
            idle = ", or too few reviews in flight to be busy" if name.startswith("rev") else ""
            notes.append(f"{name}: too little history in the log yet{idle} (neutral, not used)")
    if shown("gate_failure_ratio") and signals.gate_failure_ratio is None:
        if signals.gate_failure_rate is not None:
            # the recent hour is there: the 7-day baseline is what is short
            notes.append(
                f"gate_failure_ratio: under {MIN_BASELINE_GATE_OUTCOMES} gate outcomes in "
                "the 7-day baseline yet (neutral, not used)"
            )
        elif not shown("gate_failure_rate"):
            # the recent hour is short and gate_failure_rate's note is not there to say so
            notes.append(
                "gate_failure_ratio: too little history in the log yet (neutral, not used)"
            )
    return notes

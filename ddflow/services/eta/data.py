"""Duration tables for the ETA layer: every measured duration in one place.

Derived from :class:`ddflow.core.facts.Facts` (the metrics fold's single read of the event
log), never by a second fold and never from the clock. Each table is a list of plain
samples. "Insufficient data" is a first-class state: a figure with fewer than
``[eta] min_samples`` samples is withheld (``None``) and named under ``Tables.insufficient``,
so a thin log reads as "no answer" and not as a number fabricated from one point.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from ...config_sections.eta import EtaConfig
from ...core import progress as PR
from ...core.facts import Facts
from ..metrics.stats import percentile

#: Verdicts that actually ran a gate (a skip ran nothing): only these have a duration.
RAN = ("passed", "failed", "partial", "unavailable")


@dataclass
class Table:
    """One measured duration: its samples and the withheld stats over them (a figure under
    ``min_samples`` is ``None``; ``n`` is always the true count)."""

    name: str
    samples: list[float] = field(default_factory=list)
    samples_summary: dict[str, Any] = field(default_factory=dict)
    gate: str = ""  # for the review-rounds table: which review gate the rounds count over

    @property
    def n_samples(self) -> int:
        return len(self.samples)


@dataclass
class CycleTable(Table):
    """Cycle-time samples (first claim to first completion), plus each item's id "class"
    (the leading letters of its id) for the model's shrinkage over id prefixes."""

    id_classes: dict[str, str] = field(default_factory=dict)


def _id_class(item: str) -> str:
    """The leading letters of an item id, before its first digit: the "class" a per-prefix
    sample median belongs to (``'T'`` for ``T1``/``T2`` siblings), ``'(none)'`` for an id
    that starts with a digit."""
    out = ""
    for ch in item:
        if ch.isdigit():
            break
        out += ch
    return out or "(none)"


@dataclass
class Tables:
    """The derived duration tables returned by :func:`build`."""

    cycles: CycleTable
    gate_durations: list[Table]
    lease_waits: Table
    rounds: Table
    insufficient: dict[str, str] = field(default_factory=dict)


def _completed(facts: Facts) -> list:
    """Items with a claim and a completion: the only ones the duration tables have samples
    from (a missing duration is never a zero)."""
    spans = [
        s
        for s in facts.spans.values()
        if s.cycle_s is not None and s.completed_at is not None
    ]
    return sorted(spans, key=lambda s: s.completed_at or 0.0)


def _within(completed_at: float, facts: Facts, window_days: float) -> bool:
    """Whether a completion is inside the rolling window, the window's floor dated from the
    newest completion in the fold (a live caller folds up to now; a frozen log dates from its
    last event). ``window_days`` <= 0 keeps every completion."""
    if window_days <= 0:
        return True
    newest = max((s.completed_at or 0.0) for s in facts.spans.values())
    return completed_at >= newest - 86400.0 * window_days


def gate_durations(facts: Facts, inside_items: set[str] | None = None) -> dict[str, list[float]]:
    """gate -> seconds from a run's start to its verdict, per run. The start is the latest
    ``gate.started`` since the item's PREVIOUS verdict on that gate; a verdict with no
    ``gate.started`` falls back to the review's own recorded ``elapsed_s`` (an event neither
    timed nor started contributes no sample).``inside_items`` limits the runs to the
    rolling-window items (the window applies to every table, gate durations included)."""
    outcomes = sorted(facts.outcomes, key=lambda o: o.at)
    by_gate: dict[str, list[float]] = defaultdict(list)
    for o in outcomes:
        if o.kind not in RAN:
            continue
        if inside_items is not None and o.item not in inside_items:
            continue
        # latest start no earlier than the previous verdict of (item, gate)
        prev = max(
            (x.at for x in outcomes if x.item == o.item and x.gate == o.gate and x.at < o.at),
            default=None,
        )
        began = [t for t in facts.starts.get((o.item, o.gate), ()) if t <= o.at and (prev is None or t > prev)]
        if began:
            by_gate[o.gate].append(o.at - max(began))
        elif o.elapsed_s is not None:
            by_gate[o.gate].append(o.elapsed_s)
        # an outcome with neither a start nor an elapsed_s contributes no sample
    return dict(by_gate)


def lease_waits(facts: Facts, spans: list) -> list[float]:
    """Seconds from each completed item's first ``lease.waiting`` signal to its first claim:
    the turn spent queued behind another holder's live lease (what the contention term is
    NOT). An item that never waited has no sample, never a zero."""
    out: list[float] = []
    for s in spans:
        if not facts.waits.get(s.item):
            continue  # never waited: no sample, not a zero
        if s.first_claim is None:
            continue
        wait = min(facts.waits[s.item])
        if s.first_claim >= wait:
            out.append(s.first_claim - wait)
    return out

def _review_gate(facts: Facts, spans: list) -> str:
    """The review gate to count rounds over (both review gates belong to one item life, so
    only one can be counted). The one with the most RAN verdicts among the spans being
    counted; ``""`` when none of those ran one."""
    review = PR.REVIEW_GATES if PR.REVIEW_GATES else {"rubber_duck", "critic"}
    items = {s.item for s in spans}
    per_gate: Counter[str] = Counter(
        o.gate for o in facts.outcomes if o.item in items and o.gate in review and o.kind in RAN
    )
    return max(sorted(review), key=lambda g: per_gate[g]) if per_gate else ""


def _rounds(facts: Facts, spans: list) -> tuple[str, list[int]]:
    """The review round count per completed item over ONE review gate (both review gates
    belong to the same item life, so totalling both would over-count the reported gate's
    rounds). No verdict ran over the counted spans: no sample, never a fabricated zero."""
    gate = _review_gate(facts, spans)
    if not gate:
        return "", []
    values = [
        sum(1 for o in facts.outcomes if o.item == s.item and o.gate == gate and o.kind in RAN)
        for s in spans
    ]
    return gate, values


def build(facts: Facts, eta: EtaConfig) -> Tables:
    """Fold ``Facts`` (the one fold the caller made) into the four duration tables. Pure:
    no clock, no disk."""
    spans = _completed(facts)
    inside = [s for s in spans if _within(s.completed_at or 0.0, facts, eta.window_days)]

    cycles = CycleTable(
        name="cycle_time",
        samples=[s.cycle_s for s in inside if s.cycle_s is not None],
        id_classes={s.item: _id_class(s.item) for s in inside},
    )

    gates = [
        Table(name=name, samples=vals)
        for name, vals in sorted(gate_durations(facts, {s.item for s in inside}).items())
    ]

    waits = Table(name="lease_wait", samples=lease_waits(facts, inside))

    round_gate, round_values = _rounds(facts, inside)
    rounds = Table(
        name="review_rounds",
        samples=[float(v) for v in round_values],
        gate=round_gate,
    )

    tables = Tables(
        cycles=cycles,
        gate_durations=gates,
        lease_waits=waits,
        rounds=rounds,
    )

    apply_min_samples(tables, max(int(eta.min_samples), 1))
    return tables


def apply_min_samples(tables: Tables, min_samples: int) -> None:
    """Withhold every statistic whose table has fewer than ``min_samples`` samples and name
    it under ``insufficient``, so "insufficient data" is the answer, not a 0."""
    minimum = max(int(min_samples), 1)
    for table in tables.gate_durations:
        table.samples_summary = _stat_summary(table.samples, minimum)
    tables.cycles.samples_summary = _stat_summary(tables.cycles.samples, minimum)
    tables.lease_waits.samples_summary = _stat_summary(tables.lease_waits.samples, minimum)
    tables.rounds.samples_summary = _stat_summary(tables.rounds.samples, minimum)
    tables.insufficient = {
        table.name: f"{len(table.samples)} samples, need {minimum}"
        for table in (
            [tables.cycles, tables.lease_waits, tables.rounds] + tables.gate_durations
        )
        if len(table.samples) < minimum
    }


def _stat_summary(values: list[float], minimum: int) -> dict[str, Any]:
    if len(values) < minimum:
        return {"n": len(values), "p50_s": None, "p90_s": None, "max_s": None}
    return {
        "n": len(values),
        "p50_s": percentile(values, 50),
        "p90_s": percentile(values, 90),
        "max_s": max(values),
    }

"""What the event log says about each item, folded once for every metrics view.

Pure: ``collect(events, state)`` reads no clock and no disk. Times are epoch seconds read
with ``progress.epoch`` (the one timestamp reader); an event whose time does not parse is
left out rather than dated 1970. Nothing here is a statistic: ``summary``, ``forecast``,
``retro``, ``skips`` and ``grid`` each take the facts they need.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from ...core import progress as PR
from ...core.events import Event
from ...core.model import State
from ...core.schedule import inherited_deps

#: gate.* kinds that are a verdict on a gate (not `started` or `out_of_order`).
OUTCOMES = ("passed", "failed", "skipped", "unavailable", "partial")


@dataclass(frozen=True)
class Outcome:
    """One recorded verdict on one gate of one item."""

    item: str
    gate: str
    kind: str  # passed | failed | skipped | unavailable | partial
    at: float
    by: str = ""  # who recorded it
    model: str = ""  # the reviewer's model, when a review recorded one
    reason: str = ""
    elapsed_s: float | None = None  # a review's own recorded wall time


@dataclass
class Lease:
    item: str
    holder: str
    start: float
    end: float | None = None  # None: still held at the time the facts were read
    globs: tuple[str, ...] = ()


@dataclass
class Span:
    """One item's life in the log."""

    item: str
    kind: str = "task"
    added_at: float | None = None
    claims: list[tuple[float, str]] = field(default_factory=list)  # (at, agent)
    completed_at: float | None = None  # the FIRST completion
    ready_at: float | None = None  # when its last dependency was done; None = not known

    @property
    def first_claim(self) -> float | None:
        return self.claims[0][0] if self.claims else None

    @property
    def cycle_s(self) -> float | None:
        """First claim to first completion; None while either is missing."""
        a, b = self.first_claim, self.completed_at
        return b - a if a is not None and b is not None and b >= a else None


@dataclass
class Facts:
    spans: dict[str, Span] = field(default_factory=dict)
    outcomes: list[Outcome] = field(default_factory=list)
    #: (item, gate) -> times a run of that gate started, oldest first.
    starts: dict[tuple[str, str], list[float]] = field(default_factory=dict)
    leases: list[Lease] = field(default_factory=list)
    #: (item, at) of every bug filed against an item.
    bugs: list[tuple[str, float]] = field(default_factory=list)
    agents: dict[str, list[float]] = field(default_factory=dict)  # agent -> its event times
    state: State | None = None

    def first_event(self) -> float | None:
        times = [t for ts in self.agents.values() for t in ts[:1]]
        return min(times) if times else None


def _ordered(events: Sequence[Event]) -> list[tuple[float, Event]]:
    stamped = [(PR.epoch(e.ts), e) for e in events]
    return sorted(((t, e) for t, e in stamped if t > 0), key=lambda p: (p[0], p[1].lamport))


def _float(value) -> float | None:
    ok = isinstance(value, int | float) and not isinstance(value, bool) and value >= 0
    return float(value) if ok else None


def _outcome(at: float, ev: Event) -> Outcome | None:
    verdict = ev.kind.partition(".")[2]
    d = ev.data if isinstance(ev.data, dict) else {}
    gate = d.get("gate")
    if verdict not in OUTCOMES or not isinstance(gate, str) or not gate:
        return None
    evidence = d.get("evidence") if isinstance(d.get("evidence"), dict) else {}
    model = evidence.get("model")
    return Outcome(
        item=ev.subject,
        gate=gate,
        kind=verdict,
        at=at,
        by=str(d.get("by") or ev.agent or ""),
        model=model if isinstance(model, str) else "",
        reason=str(d.get("reason") or ""),
        elapsed_s=_float(evidence.get("elapsed_s")),
    )


def _absorb(facts: Facts, at: float, ev: Event, held: dict[str, Lease]) -> None:
    kind, d = ev.kind, ev.data if isinstance(ev.data, dict) else {}
    span = facts.spans.setdefault(ev.subject, Span(ev.subject))
    if kind in ("task.added", "phase.added"):
        span.added_at = span.added_at if span.added_at is not None else at
    elif kind == "lease.acquired":
        holder = str(d.get("holder") or ev.agent)
        span.claims.append((at, holder))
        globs = d.get("globs") if isinstance(d.get("globs"), list) else []
        if ev.subject in held:  # a re-claim closes the earlier hold
            held.pop(ev.subject).end = at
        lease = Lease(ev.subject, holder, at, None, tuple(g for g in globs if isinstance(g, str)))
        held[ev.subject] = lease
        facts.leases.append(lease)
    elif kind in ("lease.released", "lease.expired", "item.abandoned"):
        if ev.subject in held:
            held.pop(ev.subject).end = at
    elif kind == "item.completed":
        span.completed_at = span.completed_at if span.completed_at is not None else at
        if ev.subject in held:
            held.pop(ev.subject).end = at
    elif kind == "gate.started" and isinstance(d.get("gate"), str):
        facts.starts.setdefault((ev.subject, d["gate"]), []).append(at)
    elif kind == "bug.found" and isinstance(d.get("item"), str) and d["item"]:
        facts.bugs.append((d["item"], at))
    elif kind.startswith("gate."):
        out = _outcome(at, ev)
        if out is not None:
            facts.outcomes.append(out)


def _dependencies_done(facts: Facts, state: State) -> None:
    """``ready_at``: the latest first-completion among every dependency that binds the item
    (its own and its ancestors'), floored at its filing. None when a dependency has no
    completion event (imported as done, or still open): then the wait is not known."""
    for item_id, span in facts.spans.items():
        it = state.items.get(item_id)
        if it is None or span.added_at is None:
            continue
        span.kind = it.kind
        times: list[float] = [span.added_at]
        for _owner, dep in inherited_deps(state, it):
            done = facts.spans.get(dep)
            if done is None or done.completed_at is None:
                times = []
                break
            times.append(done.completed_at)
        span.ready_at = max(times) if times else None


def collect(events: Sequence[Event], state: State, now: float | None = None) -> Facts:
    """Fold ``events`` into ``Facts``. ``now`` closes the leases still held (None: they stay
    open, and callers treat an open end as 'until the facts were read')."""
    facts = Facts(state=state)
    held: dict[str, Lease] = {}
    for at, ev in _ordered(events):
        facts.agents.setdefault(ev.agent, []).append(at)
        _absorb(facts, at, ev, held)
    _dependencies_done(facts, state)
    for lease in held.values():
        lease.end = now
    return facts

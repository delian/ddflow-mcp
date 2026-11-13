"""The pure data types the metrics views fold the event log into.

Moved out of :mod:`ddflow.services.metrics.facts` (r-core-pure): a ``Facts`` value is a
plain container -- the fold (`services.metrics.facts.collect`) reads the log and fills it,
and the pure detectors in :mod:`ddflow.core.gateyield` read it back. Living here, the data
model is importable by ``core`` without reaching up into ``services``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import State


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
    #: Seconds of ``elapsed_s`` spent queued for the host suite lock or an admission slot
    #: (B1d51fb9da1); None for an older log, which never recorded it.
    waited_s: float | None = None
    #: Lines the tree differed from HEAD by when the verdict was recorded (insertions plus
    #: deletions of the evidence's ``diff_stat``); None when the evidence carried none.
    changed_lines: int | None = None
    #: A review's independence tier (family | model | none), "" for an older log or a
    #: gate no reviewer judged (D-reviewer-independence-soft).
    tier: str = ""
    #: A review round's number, when the verdict named it (``round`` or ``rounds``); None
    #: for a gate that runs no review rounds or an older log.
    round: int | None = None
    #: The tree (or reviewed head) a review round covered; "" when the verdict carried none.
    tree_sha: str = ""
    #: A merged review's id (``review_id``), "" when the verdict recorded one gate not a merge.
    review_id: str = ""
    #: The gates merged into one recorded call (``combined_gates``), oldest first; empty for
    #: a verdict that recorded a single gate (D-path-merge-answers merges bookkeeping gates).
    combined_gates: tuple[str, ...] = ()
    #: A merged / covered-by note the evidence carried (``note``), "" when it had none.
    note: str = ""


@dataclass(frozen=True)
class Landing:
    """One `worktree.merged`: when, and the commits it names (empty when the event had none)."""

    at: float
    before: str = ""  # the target branch before the merge
    after: str = ""  # ... and after it
    head: str = ""  # the branch tip that was merged


@dataclass(frozen=True)
class Finding:
    """One `review.triaged` event: a review finding and what the author did with it."""

    item: str
    gate: str
    severity: str = ""  # HIGH | MEDIUM | LOW | "" when the reviewer did not grade it
    verdict: str = ""  # confirmed | refuted
    at: float = 0.0


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
    #: item -> when each `lease.waiting` (a refused claim queued behind another lease) began.
    waits: dict[str, list[float]] = field(default_factory=dict)
    #: item -> when each `worktree.merged` landed its branch.
    merges: dict[str, list[float]] = field(default_factory=dict)
    #: item -> each `worktree.merged` with the commits it recorded (same order as ``merges``).
    landings: dict[str, list[Landing]] = field(default_factory=dict)
    #: every `review.triaged`: a finding and its verdict, oldest first.
    findings: list[Finding] = field(default_factory=list)
    state: State | None = None

    def starts_of(self, item: str) -> list[float]:
        """When any gate run of ``item`` started."""
        return [t for (it, _gate), ts in self.starts.items() if it == item for t in ts]

    def first_event(self) -> float | None:
        times = [t for ts in self.agents.values() for t in ts[:1]]
        return min(times) if times else None

"""Shared constants and the work/loop record types of the progress package."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .. import clock

#: Gates whose failure is a reviewer's verdict on the work, not a failure of the work
#: (decision D-failed-critic-not-blocking). The repeated-failure detector skips them.
REVIEW_GATES: frozenset[str] = frozenset({"rubber_duck", "critic"})

#: Kinds that represent forward progress. Used by the no-progress detector: a window
#: full of events none of which is one of these is a window in which nothing advanced.
PROGRESS_KINDS: frozenset[str] = frozenset(
    {
        "item.completed",
        "gate.passed",
        "worktree.merged",
        "task.added",
        "phase.added",
        "bug.fixed",
        "bug.invalid",
        "lesson.recorded",
        "research.recorded",
        "item.abandoned",
    }
)


#: Version-stamp and upgrade bookkeeping. An upgrade run legitimately completes nothing, so
#: these events neither count as progress nor fill the no-progress window (B629c361cc9).
BOOKKEEPING_KINDS: frozenset[str] = frozenset(
    {
        "ddflow.seen",
        "ddflow.capabilities",
        "skew.overridden",
        "upgrade.applied",
        "repair.applied",
    }
)


def epoch(ts: str) -> float:
    """Public name for `_epoch`: event timestamp -> epoch seconds, 0.0 when unparseable."""
    return _epoch(ts)


def _epoch(ts: str) -> float:
    """Event timestamp -> epoch seconds. 0.0 when unparseable.

    Needed because only `lease.acquired` carries a numeric `at`; every other event
    dates itself with the ISO `ts`. Closing an attempt with `time.time()` instead
    reported elapsed-to-NOW for work that finished days ago, which silently inflated
    every duration in the report -- the numbers looked precise and were wrong.
    """
    # A zone-less time is the host's clock here, as it always was (B-uni-clock keeps
    # each caller's reading; the log itself never writes one).
    return clock.epoch(ts, naive="local", default=0.0)


@dataclass
class Attempt:
    """One claim-to-release span on an item."""

    item: str
    holder: str
    started_at: float = 0.0
    ended_at: float = 0.0
    ended_by: str = ""  # released | expired | completed | abandoned | (open)
    gates_run: int = 0
    gates_passed: int = 0
    gates_failed: int = 0

    @property
    def seconds(self) -> float:
        """Elapsed for this attempt; elapsed-to-now only while it is still open."""
        if not self.started_at:
            return 0.0
        return max(0.0, (self.ended_at or time.time()) - self.started_at)

    @property
    def open(self) -> bool:
        return not self.ended_by


@dataclass
class ItemWork:
    item: str
    kind: str = "task"
    title: str = ""
    state: str = "open"
    attempts: list[Attempt] = field(default_factory=list)
    gate_outcomes: dict[str, list[str]] = field(default_factory=dict)
    #: gate -> the decisive verdicts of each round, a round opened by `gate.started`. A
    #: failed round and the triage that settles it share a round; see `_gate_flapping`.
    gate_rounds: dict[str, list[list[str]]] = field(default_factory=dict)
    #: gate -> (outcome, output digest, tree fingerprint) of each passed/failed run, in
    #: order. What the repeated-failure detector reads; "" where a run recorded none.
    gate_runs_evidence: dict[str, list[tuple[str, str, str]]] = field(default_factory=dict)
    commits: list[str] = field(default_factory=list)
    holders: list[str] = field(default_factory=list)
    completed_times: int = 0
    first_seen: str = ""
    last_touched: str = ""

    def add_commit(self, sha: str) -> None:
        """Record a commit once: merge and complete both name the landing's sha."""
        if sha not in self.commits:
            self.commits.append(sha)

    def open_gate_round(self, gate: str) -> None:
        """`gate.started` opens a round of the gate's reviews."""
        self.gate_rounds.setdefault(gate, []).append([])

    def add_round_verdict(self, gate: str, outcome: str) -> None:
        """File a decisive verdict under the gate's open round (one is made if none was)."""
        if outcome not in ("passed", "failed"):
            return
        rounds = self.gate_rounds.setdefault(gate, [])
        if not rounds:
            rounds.append([])
        rounds[-1].append(outcome)

    def add_gate_run(self, gate: str, outcome: str, evidence: Any) -> None:
        """Keep a decisive run's output digest and tree, for the repeated-failure check."""
        if outcome not in ("passed", "failed"):
            return
        ev = evidence if isinstance(evidence, dict) else {}
        self.gate_runs_evidence.setdefault(gate, []).append(
            (outcome, str(ev.get("output_digest") or ""), str(ev.get("tree_sha") or ""))
        )

    @property
    def total_seconds(self) -> float:
        return sum(a.seconds for a in self.attempts)

    @property
    def gate_runs(self) -> int:
        return sum(len(v) for v in self.gate_outcomes.values())

    def summary(self) -> dict[str, Any]:
        return {
            "item": self.item,
            "kind": self.kind,
            "title": self.title,
            "state": self.state,
            "attempts": len(self.attempts),
            "held_seconds": round(self.total_seconds, 1),
            "gate_runs": self.gate_runs,
            "commits": len(self.commits),
            "holders": sorted(set(self.holders)),
            "completed_times": self.completed_times,
        }


@dataclass
class LoopFinding:
    """One detected loop. ``kind`` names the pattern; ``detail`` carries the evidence."""

    kind: str
    item: str
    count: int
    threshold: int
    detail: str
    severity: str = "warn"  # warn | block
    #: The gate and the tree it last failed on, for the findings that are about one gate.
    gate: str = ""
    tree_sha: str = ""

    def render(self) -> str:
        return f"[{self.severity.upper()}] {self.kind} · {self.item}: {self.detail}"

"""Item states and gate outcomes: the vocabulary every fold record shares.

Part of the former `ddflow/core/records.py`, split into submodules that stay under
30k chars each (rule r-small-files). This submodule holds the derived item-state
constants, the `GateOutcome` enum, its marks and exit codes, and the gate record
dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ..outcome import FAIL, NOTHING, OK

# Item states. These are DERIVED, never written: an item's state is a function of the
# events about it. A state field that can be set directly is a field that can drift
# from the events that produced it, which is the whole defect class this design closes.
OPEN, RUNNING, BLOCKED, DONE, ABANDONED = "open", "running", "blocked", "done", "abandoned"
#: Waiting on a pull request (RESEARCH R16). Distinct from RUNNING because nobody holds
#: it: the lease is released when the request opens, so the agent can take the next
#: task. As RUNNING-without-a-lease it would read as a crash to `recover` and as
#: interrupted work to `next`, and every parked review would be offered to a second agent.
REVIEW = "review"


#: The single source for gate outcomes: everything that validates, renders or maps an
#: outcome imports from here.
class GateOutcome(StrEnum):
    """What a gate was recorded with: the one vocabulary, and the one definition of
    whether an outcome is enough.

    ``unavailable`` is a first-class outcome beside pass and fail. A reviewer that could
    not run is not a reviewer that found nothing."""

    PASSED = "passed"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"
    PARTIAL = "partial"
    SKIPPED = "skipped"

    def satisfies(self, required: bool) -> bool:
        """Nothing is left to do for the gate: it passed, or it was skipped and is not
        required. The ONE definition of a settled gate -- the pipeline view, the brief,
        the completion check and the record check each used to spell their own, and a
        skipped required gate read as done in two of them and blocking in the others."""
        return self is GateOutcome.PASSED or (self is GateOutcome.SKIPPED and not required)

    @property
    def mark(self) -> str:
        """The single-character mark the board and the gate status view draw."""
        return _MARKS[self]

    @property
    def exit(self) -> int:
        """The exit a command gate with this outcome ends with. `unavailable` and
        `partial` are NOTHING: the gate could not report, which is neither a pass nor
        a failure of the work."""
        return _EXITS[self]

    @classmethod
    def settled(cls, outcome: str, required: bool) -> bool:
        """`satisfies` for a recorded string: an empty or unknown outcome never does."""
        try:
            return cls(outcome).satisfies(required)
        except ValueError:
            return False


_MARKS = {
    GateOutcome.PASSED: "x",
    GateOutcome.FAILED: "!",
    GateOutcome.UNAVAILABLE: "?",
    GateOutcome.PARTIAL: "~",
    GateOutcome.SKIPPED: "-",
}
_EXITS = {
    GateOutcome.PASSED: OK,
    GateOutcome.SKIPPED: OK,
    GateOutcome.FAILED: FAIL,
    GateOutcome.UNAVAILABLE: NOTHING,
    GateOutcome.PARTIAL: NOTHING,
}

#: The outcomes a gate can be RECORDED with. `started` is deliberately absent: it is an
#: event kind in the `gate.` namespace, not an outcome, and anything that accepts it as
#: one lets a caller record a gate as having begun and never finished.
#:
#: `gate.` is a NAMESPACE, not a synonym for "an outcome was recorded" --
#: `gate.out_of_order` lives there too and is neither. A consumer matching the PREFIX
#: counted that as a gate run, which is how adding one event kind silently changed a
#: metric two modules away.
GATE_OUTCOMES: tuple[str, ...] = tuple(o.value for o in GateOutcome)

#: The gate outcomes a ROUTINE completion may finish over without a person: a gate that could
#: not run (`unavailable`), one that reported partway (`partial`), or one the agent deliberately
#: set aside with a recorded reason (`skipped`). The ONE enumeration of those three: the
#: standing `complete_unavailable` predicate (`approvals.policy`) reads the tuple itself, and the
#: completion coverage-gap set (`completion.rules`) adds the `Skipped` member through
#: `_skipped_gaps` and the other two through `GateStatus.unavailable` ("unavailable" + "partial",
#: the render set in `gates.outcomes`) (Bb67dc543b6).
COVERAGE_GAP_OUTCOMES: tuple[str, ...] = (
    GateOutcome.UNAVAILABLE.value,
    GateOutcome.PARTIAL.value,
    GateOutcome.SKIPPED.value,
)

#: Outcome -> single-character mark, used by both the board and the gate status view
#: ("" -- no outcome yet -- is a blank).
OUTCOME_MARK: dict[str, str] = {**{o.value: o.mark for o in GateOutcome}, "": " "}


@dataclass
class GateRecord:
    """One gate's outcome for one item.

    ``unavailable`` is a first-class outcome, distinct from both pass and fail. A
    reviewer that could not run is not a reviewer that found nothing; recording the
    two the same way is how a whole review silently disappears from a pipeline.
    """

    gate: str
    outcome: str = ""
    at: str = ""
    by: str = ""
    reason: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def is_blocking_failure(self) -> bool:
        return self.outcome == GateOutcome.FAILED


@dataclass
class GateRun:
    """One recorded gate outcome, kept for good: `Item.gates` holds only the LAST one per
    gate, and a re-claim, a skip or a delta replaces it. The log's `started` and
    `out_of_order` events are not outcomes and are not here."""

    gate: str
    outcome: str
    at: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

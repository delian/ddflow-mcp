"""The `[gates]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass, field

from ._docs import _doc


@dataclass
class GatesConfig:
    """The per-task and per-phase quality pipelines."""

    task_pipeline: list[str] = field(
        default_factory=lambda: [
            "research",
            "rules",
            "implement",
            "rubber_duck",
            "critic",
            "standards",
            "unit_tests",
            "bug_hunt",
            "dedupe",
            "merge",
        ]
    )
    phase_pipeline: list[str] = field(
        default_factory=lambda: [
            "research",
            "tasks",
            "unit_tests",
            "bug_hunt",
            "dedupe",
            "live_test",
            "corrections",
            "docs",
            "merge",
        ]
    )
    required: list[str] = field(
        default_factory=lambda: [
            "implement",
            "unit_tests",
            "merge",
        ]
    )
    unavailable_is_failure: bool = False
    allow_skip_with_reason: bool = True
    require_outcome: bool = True
    enforce_order: str = "warn"
    promotion_pipeline: list[str] = field(default_factory=lambda: ["unit_tests", "merge"])
    rate_min_runs: int = 5
    rate_max_fail: float = 0.9
    evidence_required: list[str] = field(
        default_factory=lambda: [
            "unit_tests",
            "rubber_duck",
            "critic",
            "standards",
            "docs",
        ]
    )


_doc(
    "gates",
    "task_pipeline",
    "Ordered gate ids every TASK passes through. Reorder or trim per project; ids must exist in [gate.*] definitions.",
)
_doc(
    "gates",
    "phase_pipeline",
    "Ordered gate ids every PHASE passes through. 'tasks' is the fan-out point where member tasks run (in parallel where dependencies allow). 'docs' reviews and updates the documentation for everything the phase changed, before it merges.",
)
_doc(
    "gates",
    "promotion_pipeline",
    "Ordered gate ids a PROMOTION task passes through ([flow].environments). Short by default: a promotion carries work that already passed its own pipeline, so re-running research and review on it measures nothing. Add a human gate here to require a person's sign-off on a deploy.",
)
_doc(
    "gates",
    "required",
    "Gates whose failure BLOCKS completion. Everything else records its outcome and lets the pipeline continue — advisory vs blocking is an explicit field, never a convention.",
)
_doc(
    "gates",
    "require_outcome",
    "Every gate in the pipeline must carry SOME recorded outcome before an item completes — passed, failed, unavailable, partial, or an explicit `gate skip --reason`. Silence is not a pass, for the same reason UNAVAILABLE is not. With this false, `required` alone blocks and the other gates become documentation. Default true: the fixed order is the point of the pipeline.",
)
_doc(
    "gates",
    "enforce_order",
    "What `gate record` does when an EARLIER pipeline gate has no outcome yet: 'warn' (record it, say so), 'block' (refuse), 'off'. Default 'warn' — reviewing before the tests run is sometimes deliberate, skipping research entirely never is, and `require_outcome` is what catches the latter.",
)
_doc(
    "gates",
    "unavailable_is_failure",
    "If true, a gate that could not run (tool missing, endpoint down) blocks like a failure. Default false, but UNAVAILABLE is always recorded distinctly and NEVER as a pass — that distinction is the point.",
)
_doc(
    "gates",
    "allow_skip_with_reason",
    "Permit `ddflow gate skip <id> --reason '...'`. The reason is mandatory and is recorded in the event log, so a skip is auditable rather than invisible.",
)
_doc(
    "gates",
    "evidence_required",
    "Gates that must attach evidence (command, exit code, output digest) for their outcome to count. A bare 'it passed' from these gates is rejected.",
)

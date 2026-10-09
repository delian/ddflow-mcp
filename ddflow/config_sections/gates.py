"""The `[gates]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, knob
from ._kinds import kind_pipelines_problem


@declare("gates")
@dataclass
class GatesConfig:
    """The per-task and per-phase quality pipelines."""

    task_pipeline: list[str] = knob(
        factory=lambda: [
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
        ],
        doc="Ordered gate ids every TASK passes through. Reorder or trim per project; ids must exist in [gate.*] definitions.",
    )
    phase_pipeline: list[str] = knob(
        factory=lambda: [
            "research",
            "tasks",
            "unit_tests",
            "bug_hunt",
            "dedupe",
            "live_test",
            "corrections",
            "docs",
            "merge",
        ],
        doc="Ordered gate ids every PHASE passes through. 'tasks' is the fan-out point where member tasks run (in parallel where dependencies allow). 'docs' reviews and updates the documentation for everything the phase changed, before it merges.",
    )
    kind_pipelines: dict[str, list[str]] = knob(
        factory=dict,
        doc='Per item-kind gate pipelines that replace the built-in one: a table from an item kind (task, phase, bug, doc, research, job) to its ordered gate ids, e.g. { doc = ["implement", "merge"] }. A kind not named here runs `task_pipeline` (`phase_pipeline` for a phase); a promotion task keeps `promotion_pipeline`. Empty by default, so every kind runs as before. `ddflow workflow --json` shows each kind\'s effective pipeline.',
        check=kind_pipelines_problem,
    )
    required: list[str] = knob(
        factory=lambda: [
            "implement",
            "unit_tests",
            "merge",
        ],
        doc="Gates whose failure BLOCKS completion. Everything else records its outcome and lets the pipeline continue — advisory vs blocking is an explicit field, never a convention.",
    )
    unavailable_is_failure: bool = knob(
        False,
        doc="If true, a gate that could not run (tool missing, endpoint down) blocks like a failure. Default false, but UNAVAILABLE is always recorded distinctly and NEVER as a pass — that distinction is the point.",
    )
    allow_skip_with_reason: bool = knob(
        True,
        doc="Permit `ddflow gate skip <id> --reason '...'`. The reason is mandatory and is recorded in the event log, so a skip is auditable rather than invisible.",
    )
    require_outcome: bool = knob(
        True,
        doc="Every gate in the pipeline must carry SOME recorded outcome before an item completes — passed, failed, unavailable, partial, or an explicit `gate skip --reason`. Silence is not a pass, for the same reason UNAVAILABLE is not. With this false, `required` alone blocks and the other gates become documentation. Default true: the fixed order is the point of the pipeline.",
    )
    enforce_order: str = knob(
        "warn",
        doc="What `gate record` does when an EARLIER pipeline gate has no outcome yet: 'warn' (record it, say so), 'block' (refuse), 'off'. Default 'warn' — reviewing before the tests run is sometimes deliberate, skipping research entirely never is, and `require_outcome` is what catches the latter.",
        choices=("warn", "block", "off"),
        strictest=("block", "a gate recorded out of order is refused"),
    )
    promotion_pipeline: list[str] = knob(
        factory=lambda: ["unit_tests", "merge"],
        doc="Ordered gate ids a PROMOTION task passes through ([flow].environments). Short by default: a promotion carries work that already passed its own pipeline, so re-running research and review on it measures nothing. Add a human gate here to require a person's sign-off on a deploy.",
    )
    rate_min_runs: int = knob(
        5,
        doc="How many DECISIVE runs a gate needs before its failure rate is judged. One failure out of one run is 100% and means nothing, so a low value turns a new gate's first red into a finding — which is the crying-wolf failure this check exists to prevent. A skipped gate is not a run.",
    )
    rate_max_fail: float = knob(
        0.9,
        doc="Failure rate (0.0-1.0) at which a gate is reported as failing on nearly everything. 'A gate that fails on everything is worse than no gate: it trains the next reader to skip it.' At or above this, the gate is flaky or measuring a moving target and re-running it will not converge -- the remedy is to repair the gate, not the work.",
    )
    evidence_required: list[str] = knob(
        factory=lambda: [
            "unit_tests",
            "rubber_duck",
            "critic",
            "standards",
            "docs",
        ],
        doc="Gates that must attach evidence (command, exit code, output digest) for their outcome to count. A bare 'it passed' from these gates is rejected.",
    )
    unit_tests_scope: str = knob(
        "selected",
        doc="What the unit_tests gate of a BUG FIX or SMALL task runs (decision D-gate-economy 1): 'selected' (default) runs only the tests its change reaches -- `ddflow tests --item <id>`, plus the regression tests of the bugs it fixes -- and lists in the evidence which ran and why; 'full' runs the whole suite as every other item does. Selected only once the item's `ci` gate -- the whole suite on the branch merged with the base -- has PASSED on the clean commit its tree holds now (ci tests the committed HEAD) (a fix spent about 2.1 full-suite runs of 12 minutes each before this). A ci with no outcome, skipped, unavailable, failed, run over uncommitted edits or on another commit, a phase, a promotion, a larger task, and a selection that cannot be made (git cannot say what changed, no test reaches the change, the command does not run pytest) run the whole suite, and the evidence says why. Change it for the project (`ddflow config gates.unit_tests_scope full`), for this machine (add --local), per run (DDFLOW_GATES_UNIT_TESTS_SCOPE), or over MCP with `ddflow_configure`.",
        choices=("selected", "full"),
        strictest=(
            "full",
            "runs the whole suite at the gate, so nothing a selection misses can pass it",
        ),
    )
    unit_tests_small_lines: int = knob(
        150,
        doc='A task with fewer changed lines than this (added plus removed since its base, `.ddflow/` excluded; default 150) is SMALL, and its unit_tests gate runs only the selected tests under `gates.unit_tests_scope = "selected"`. A bug-fix task is selected whatever its size. 0 = no task is small (bug fixes still are).',
    )

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
        doc='Per item-kind gate pipelines that replace the built-in one: a table from an item kind (task, phase, bug, doc, research, job) to its ordered gate ids, e.g. { doc = ["implement", "merge"] }. A kind not named here runs `task_pipeline` (`phase_pipeline` for a phase); a promotion task keeps `promotion_pipeline`. Empty by default, so every kind runs as before. `ddflow workflow` lists each kind that differs, and `--json` every kind\'s effective pipeline.',
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
        doc="What the unit_tests gate of a BUG FIX or SMALL task runs (decision D-gate-economy 1): 'selected' (default) runs only the tests its change reaches -- `ddflow tests --item <id>`, plus the regression tests of the bugs it fixes -- and lists in the evidence which ran and why; 'full' runs the whole suite as every other item does. Selected only once the item's `ci` gate -- the whole suite, or with `[ci].tests_scope = 'selected'` its own selection (this gate then runs only the tests of ITS selection that ci did not take, normally none; with a scoped ci any item, not only a bug fix or small task), on the branch merged with the base -- has PASSED on the clean commit its tree holds now (ci tests the committed HEAD) (a fix spent about 2.1 full-suite runs of 12 minutes each before this). A ci with no outcome, skipped, unavailable, failed, run over uncommitted edits or on another commit, a phase, a promotion, a larger task, and a selection that cannot be made (git cannot say what changed, no test reaches the change, the command does not run pytest) run the whole suite, and the evidence says why. Change it for the project (`ddflow config gates.unit_tests_scope full`), for this machine (add --local), per run (DDFLOW_GATES_UNIT_TESTS_SCOPE), or over MCP with `ddflow_configure`.",
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
    unit_tests_max_files: int = knob(
        100,
        doc="The most test files a selected unit_tests run (and `ddflow tests --item`) takes (default 100; 0 = no bound). Past it the farthest tests are cut -- two-hop importers first, then direct importers, never a test the change itself touches -- and the cut set is recorded in the gate evidence. The whole suite still runs in the merge queue, on the integration cadence and at phase end (D-test-economy-r2).",
    )
    unit_tests_hub_importers: int = knob(
        100,
        doc="A module imported by this many files or more (default 100; 0 = no hubs) is a HUB: the selection's import walk does not continue through it (a test that only reaches the change by importing a hub is not selected), and a change to a hub itself runs the whole suite, since it reaches everything. Raise it to select more, lower it to select less.",
    )
    unit_tests_max_hops: int = knob(
        2,
        doc="How far along the import graph a test still counts as reaching a change (default 2): it imports the changed module (1), or imports a module that does (2). Unbounded, the layer that imports everything makes every test reach every module.",
    )
    unit_tests_name_max_tests: int = knob(
        30,
        doc="A distinctive identifier the change defines, edits or removes selects the tests that spell it, unless more than this many tests do (default 30): then it is a word, not a name.",
    )
    scrub_env: list[str] = knob(
        factory=lambda: [
            "DDFLOW_AGENT",
            "DDFLOW_SESSION_ID",
            "CLAUDE_SESSION_ID",
            "CLAUDE_CODE_SESSION_ID",
        ],
        doc="Environment variables removed from the environment a gate command and the CI command run with. Default: the identity variables ddflow reads (`DDFLOW_AGENT` and the session ids `DDFLOW_SESSION_ID`, `CLAUDE_SESSION_ID`, `CLAUDE_CODE_SESSION_ID`): they name the caller, not the suite, and a shell that exports one made tests that read the caller's identity fail in the gate while passing without it. Add a variable a project's tests must not inherit; list one in `keep_env` to preserve it.",
    )
    keep_env: list[str] = knob(
        factory=list,
        doc='Variables a gate command keeps although `scrub_env` removes them, e.g. `["DDFLOW_AGENT"]` for a gate whose command runs ddflow as the calling agent. Empty by default. `[ci].keep_env` does the same for the CI command alone.',
    )

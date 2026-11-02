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

    max_run_timeout_s: int = knob(
        21600,
        doc="The longest a single `gate run <id> <gate> --timeout N` (or the MCP `timeout` parameter, or `ddflow ci run --timeout N`) may ask a command gate to run, in seconds. A run needs the override on a loaded host without anyone editing the shared `[ci].timeout_s` / gate timeout; a larger request is refused (exit 3) naming this knob, so one agent cannot hold the host suite lock for days.",
    )
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
    doc_pipeline: list[str] = knob(
        factory=lambda: ["rules", "implement", "standards", "dedupe", "merge"],
        doc="Ordered gate ids a DOC item passes through (`task add --kind doc`): documents and generated output, so no unit_tests, critic or bug hunt. `[gates].kind_pipelines.doc` replaces it. `required` gates still bind when they appear here.",
    )
    doc_globs: list[str] = knob(
        factory=lambda: ["docs/**", "*.md", "**/*.md"],
        doc="A TASK (or `research` item) whose ACTUAL changed paths ALL fall inside these runs `doc_pipeline` and needs no cross-family reviewer: a research or documentation task made without `--kind doc` (the MCP `task_add` has none) would otherwise need `unit_tests` and an independent reviewer for work that changed no code. The diff decides, never the declared globs (a required gate is not excused by what a task says it will touch); with no readable diff the task pipeline applies. One path outside them keeps the task pipeline, and so does a `research` item whose kind `[gates].kind_pipelines` names itself. Empty turns it off.",
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
        doc='Per item-kind gate pipelines that replace the built-in one: a table from an item kind (task, phase, bug, doc, research, job) to its ordered gate ids, e.g. { doc = ["implement", "merge"] }. A kind not named here runs `task_pipeline` (`phase_pipeline` for a phase); a promotion task keeps `promotion_pipeline`. Empty by default; a `doc` item (`task add --kind doc`) runs `doc_pipeline` unless this names it. `ddflow workflow` lists each kind that differs, and `--json` every kind\'s effective pipeline.',
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
    merge_blocked_by_failed: list[str] = knob(
        factory=lambda: ["ci"],
        doc="Gates whose latest outcome FAILED, or was skipped over a failed run, makes `ddflow merge` refuse the item: landing it can turn the base red. The way past is a run that passes, or `gate record --outcome unavailable|partial --reason`. Empty turns the check off.",
    )
    unavailable_is_failure: bool = knob(
        False,
        doc="If true, a gate that could not run (tool missing, endpoint down) blocks like a failure. Default false, but UNAVAILABLE is always recorded distinctly and NEVER as a pass — that distinction is the point.",
    )
    allow_skip_with_reason: bool = knob(
        True,
        doc="Permit `ddflow gate skip <id> --reason '...'`. The reason is mandatory and is recorded in the event log, so a skip is auditable rather than invisible.",
    )
    skip_followup: str = knob(
        "auto",
        doc="When a gate is recorded skipped (`ddflow gate skip <id> <gate> --reason ...`, or a hand-recorded `outcome=skipped`), `auto` files ONE future follow-up task whose scope is ONLY to re-run that gate on that item later (run `ddflow gate run <id> <gate>` and record the honest outcome); the follow-up is the log entry, the reminder and the effort to re-run the gate -- not to repair the gate-check code nor to redo the item's payload. It is dropped when the item is abandoned, and settles when the item completes or the gate is re-recorded non-skipped. `warn` reports the follow-up in the skip output and files nothing; `off` does neither. A skip today records nothing extra: only `auto` adds the follow-up.",
        choices=("auto", "warn", "off"),
        strictest=("auto", "always files the follow-up, so a skipped gate is never forgotten"),
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
    baseline_red_auto: str = knob(
        "on",
        doc="Whether a ci or unit_tests gate whose every failing test ddflow MEASURED as failing on the base branch too (evidence `baseline.red`), and none of which sits in a file the item changed or reaches by import, is accepted AUTOMATICALLY (decision D-baseline-red-auto-accept): the gate stays `partial` and shows as accepted-baseline-red, never passed; `complete` warns; the coordinator is told through the inbox and the approvals log, naming the test ids; a bug tagged baseline-red is filed or linked by test id. 'on' (default) accepts; 'off' leaves every such partial to a person (`ddflow approve <id> <gate>`). A failed gate, a new failing test, an unavailable probe and a failure in the item's own files are never accepted. Change it for the project (`ddflow config gates.baseline_red_auto off`), for this machine (add --local), per run (DDFLOW_GATES_BASELINE_RED_AUTO), or over MCP with `ddflow_configure`.",
        choices=("on", "off"),
        strictest=("off", "every baseline-red partial waits for a person"),
    )
    baseline_cache_ttl_s: int = knob(
        86400,
        doc="Seconds a measurement of the base branch (which tests fail on one base commit) is reused (default 86400; 0 = never reuse). It is kept per base sha under `.ddflow/local/baseline/`, shared by every item, so a second item costs a comparison, not a run; it expires on its own when the base moves, since a new base has a new sha.",
    )
    baseline_red_rate_warn: int = knob(
        10,
        doc="Automatic baseline-red acceptances in the last 24 hours from which `ddflow doctor` notes that the base branch is red and what to fix (default 10; 0 = never note).",
    )
    baseline_red_notify_to: str = knob(
        "coordinator",
        doc="The inbox (agent name) told, once, when a baseline-red partial is accepted automatically (`[gates].baseline_red_auto`): the message names the item, the gate and the failing test ids and the bug. Empty = no inbox message (the approvals log and `ddflow doctor` still show it).",
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
    standards_wait_s: int = knob(
        900,
        doc="Seconds `python -m ddflow.services.roborev_branch` (the standards gate's roborev run on the item's COMBINED branch diff) waits for the roborev job before it gives up (default 900; 0 = enqueue and return). A job still running at the limit is left running and reported with its id and `roborev wait --job <id>`: the gate is recorded `unavailable` with that reason, never passed.",
    )

"""The `[ci]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, knob

#: `[ci].on_merge`'s values.
CI_ON_MERGE_MODES = ("off", "fast", "full")
#: `[ci].tests_scope`'s values.
CI_TESTS_SCOPES = ("selected", "full")


@declare("ci")
@dataclass
class CiConfig:
    """The CI gate: the checks that must pass on the merge result before a task is done."""

    command: str = knob(
        "",
        doc="The command the `ci` gate and `ddflow ci run` execute in a scratch worktree of the branch merged with the base. Empty (default): the project's own pre-push stage, `pre-commit run --hook-stage pre-push`, run over the diff the check is about (the branch against its base, or the range a merge added) when .pre-commit-config.yaml exists; with neither, the gate is UNAVAILABLE (never a pass). Set it to run something else, e.g. `uv run ruff check . && uv run pytest -q`.",
    )
    base: str = knob(
        "",
        doc="The branch merged into the task's branch before the checks run, so an interaction with what the base has become is tested (a parallel merge's effect). Empty (default): the repository's default branch.",
    )
    ruff_autofix: bool = knob(
        True,
        doc="Whether the `ci` gate auto-corrects ruff lint/format drift it finds on an item's branch before it can merge. When a `ddflow ci run` for an item fails on the PRE-COMMIT ruff hooks (`ruff check`, `ruff format --check`) and this is on, the run also runs `ruff format` and `ruff check --fix` over the changed Python files in the item's own worktree and records the corrections (``reformatted``/``fixed`` files) in the gate evidence; the gate stays failed until the agent commits them and re-runs, so a file with drift cannot reach the merge (the recurring `git push` pre-commit failure, Bci-ruff-format-check-ci-lint-*). Ruff is run as `ruff` when it is on PATH, else `uv run ruff` (the pinned venv the `repo: local` hooks use); with neither the correction is recorded unavailable. false: only report the failure.",
    )
    pins_precheck: bool = knob(
        True,
        doc="Before the default CI command runs, check the project's pinned outputs (`ddflow pins regenerate --check`, the recipes in `[pins].recipes_file`) in the merge result. A pin that was current on the branch can be stale once the base moved (README tables, snapshots); the run then ends at once as failed, naming the stale pins and the fix (`ddflow pins regenerate`), instead of running the whole suite to a failure that does not say so. A pin that cannot be checked is skipped, never a failure. false: go straight to the command. A custom `--command` is never prechecked.",
    )
    timeout_s: int = knob(
        3600,
        doc="Seconds the CI command may run before the gate records UNAVAILABLE. Default 3600: sized for the whole test suite (`ci run --full`, the merge queue's batch); an item's selected run is far shorter.",
    )
    workers: str = knob(
        "auto",
        doc="The pytest-xdist worker count that replaces `{workers}` in the gate commands and in `ddflow tests` output: `auto` (default) computes it when the tests run, clamp(floor(cores * [schedule.signals.load_per_core].high - current load average), min_workers, max_workers), halved above the memory_pressure high mark, minimum at its critical mark, and never above the number of test files the command names; a number fixes it. The inputs are logged in the gate evidence (D-agent-test-etiquette).",
    )
    min_workers: int = knob(
        1,
        doc="The fewest workers `[ci].workers = auto` may compute, at least 1.",
    )
    suite_min_workers: int = knob(
        8,
        doc="The fewest workers a run that holds the host-wide full-suite lock gets, however loaded the host (capped by max_workers; critical memory pressure still wins). The lock serialises every agent's full suite, so a holder cut to `-n 1` by the load clamp kept the rest waiting for hours. 1 turns the floor off.",
    )
    max_workers: int = knob(
        48,
        doc="The most workers `[ci].workers = auto` may compute, however idle the host.",
    )
    max_concurrent_suites: int = knob(
        1,
        doc="How many full test suites (the CI-parity run of `ddflow ci run --full`, the merge queue, the after-merge check and the `unit_tests` gate when its command names no test file) may run at once on this host, counted across every project and agent of the user through a lock in the user's state directory. A run over the limit waits `suite_wait_s`, then is UNAVAILABLE ('blocked on <holder> since <time>'), never a pass. A selected run queues only when it is heavy: see `selected_lock_s`.",
    )
    selected_lock_tests: int = knob(
        20,
        doc="A SELECTED `ddflow ci run` (tests_scope = selected) that reaches more test files than this queues for the host-wide full-suite slot like the whole suite does, when too few measured runs exist for `selected_lock_s` to decide. The always-run guards are not counted: every item runs them, so they say nothing of the change. Default 20; 0: a selected run never queues, whatever is measured. Either way its pytest workers are the computed count (`[ci].workers`, at most `max_workers`, never above the files), not one per core (B1401457afd).",
    )
    selected_lock_s: int = knob(
        600,
        doc="A SELECTED `ddflow ci run` predicted to run longer than this many seconds queues for the host-wide full-suite slot. The prediction is the median worker-seconds per selected test file of past `ci` gates (their run time without the queue wait and the always-run guards), times the selected files, over the workers `[ci].workers` computes now: a loaded host grants fewer workers, so the same selection predicts longer. Default 600. The gate evidence records the decision as `admission`: `basis` is `measured` only when past runs gave the prediction, else `file_count`. 0: only `selected_lock_tests` decides.",
    )
    selected_lock_min_runs: int = knob(
        5,
        doc="The fewest measured `ci` runs `selected_lock_s` needs; with fewer, `selected_lock_tests` decides. A run measures only when it ended in a verdict, its selected tests ran, and it recorded its seconds and worker count. At least 1.",
    )
    selected_lock_runs: int = knob(
        50,
        doc="How many of the newest measured `ci` runs the prediction of `selected_lock_s` takes its median from, which also bounds the search of the log. At least 1.",
    )
    suite_wait_s: int = knob(
        1800,
        doc="Seconds a full-suite run waits for a free slot (`max_concurrent_suites`) before it reports who holds it.",
    )
    suite_queue_stale_s: int = knob(
        120,
        doc="Seconds a run queued for the full-suite lock may go without a heartbeat before the queue passes over it (a stopped process). A waiter killed outright leaves the queue within about a second. Waiters are served first in, first out.",
    )
    suite_queue_grace_s: int = knob(
        120,
        doc="Seconds a run that gave up waiting for the full-suite lock keeps its place in the queue, so a retry within it is not sent to the back.",
    )
    keep_env: list[str] = knob(
        factory=list,
        doc="Variables the CI command keeps although `[gates].scrub_env` removes them from a gate's or the CI command's environment (the caller's identity variables). Empty by default; `[gates].keep_env` applies to every gate command as well.",
    )
    #: Health of the base after a merge lands: off | fast (lint, format, security: the
    #: pre-commit set without the test hooks) | full (the whole command).
    on_merge: str = knob(
        "fast",
        doc="Health check of the base right after a merge lands: off | fast (default: the project's pre-commit set without the test hooks, `SKIP=tests,scenarios`; an explicit [ci].command runs as written) | full (the whole command). The result is recorded as `ci.result`; each failing check files one bug and fix task while the bug is open. A project with no CI command is left alone.",
        choices=CI_ON_MERGE_MODES,
        strictest=("full", "the whole CI command runs after a merge"),
    )
    tests_scope: str = knob(
        "selected",
        doc="Which tests `ddflow ci run` runs for ONE item (D-test-economy-r2), selected | full. selected (default) runs every non-test pre-push hook plus only the tests the change reaches (`ddflow tests --item`), records the scope, the selected count and the cut set in the gate evidence, and runs the whole suite instead when the selector says so (a hub, conftest, pyproject, the pre-commit config, an unknown file type, or no test reaches the change); full runs the whole suite. `ddflow ci run --full`, and the merge queue integrator (one run per batch) always run the whole suite; a ref that is no item (a bare sha) takes the same selection. Applies to the default pre-push command only: an explicit [ci].command runs as written.",
        choices=CI_TESTS_SCOPES,
        strictest=("full", "every item's ci run takes the whole suite"),
    )
    always_tests: list[str] = knob(
        factory=lambda: [
            "tests/test_constants_ratchet.py",
            "tests/test_layering*.py",
            "tests/golden/**",
            "tests/test_uc_surf_setup_ops_transcript.py",
            "tests/test_uc_surf_loop_transcript.py",
            "tests/test_readme_*.py",
            "tests/test_mcp_tool_budget.py",
            "tests/test_mcp_parity.py",
        ],
        doc="Test-file globs a selected `ddflow ci run` (see `tests_scope`) runs for EVERY item in addition to the tests its change reaches, plus any test file declaring `ALWAYS = True` at module level. They are the repo-wide guards and pins (the constants ratchet, the layering guard, golden snapshots, the setup-ops and loop transcripts, the README table pins, the tool budget, CLI/MCP parity: the ones that have broken main past a selection; measured about 30 s plus the loop transcript's one long test). Left out of the default for their cost, add them when they bite: `tests/test_architecture_guards.py` (18 s), `tests/test_compat*.py`, `tests/test_uc_surf_registry_rule_transcript.py` (37 s): their input is the whole code base, so no import closure can know a change affects them. Recorded in the gate evidence as `always` (count, files) and `always_s` (the seconds they cost); an empty closure still runs the whole suite (which holds them). Empty list: only the selection (and any `ALWAYS = True` file) runs.",
    )

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
        doc="The command the `ci` gate and `ddflow ci run` execute in a scratch worktree of the branch merged with the base. Empty (default): the project's own pre-push stage, `pre-commit run --hook-stage pre-push --all-files`, when .pre-commit-config.yaml exists; with neither, the gate is UNAVAILABLE (never a pass). Set it to run something else, e.g. `uv run ruff check . && uv run pytest -q`.",
    )
    base: str = knob(
        "",
        doc="The branch merged into the task's branch before the checks run, so an interaction with what the base has become is tested (a parallel merge's effect). Empty (default): the repository's default branch.",
    )
    timeout_s: int = knob(
        3600,
        doc="Seconds the CI command may run before the gate records UNAVAILABLE. Default 3600: sized for the whole test suite (`ci run --full`, the merge queue's batch); an item's selected run is far shorter.",
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
        doc="Which tests `ddflow ci run` runs for ONE item (D-test-economy-r2), selected | full. selected (default) runs every non-test pre-push hook plus only the tests the change reaches (`ddflow tests --item`), records the scope, the selected count and the cut set in the gate evidence, and runs the whole suite instead when the selector says so (a hub, conftest, pyproject, the pre-commit config, an unknown file type, or neither a test nor an `always_tests` file to run); full runs the whole suite. `ddflow ci run --full`, the merge queue integrator (one run per batch) and a run of a ref that is no item always run the whole suite. Applies to the default pre-push command only: an explicit [ci].command runs as written.",
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
        ],
        doc="Test-file globs a selected `ddflow ci run` (see `tests_scope`) runs for EVERY item in addition to the tests its change reaches, plus any test file declaring `ALWAYS = True` at module level. They are the repo-wide guards and pins (the constants ratchet, the layering guard, golden snapshots, the setup-ops and loop transcripts, the README table pins, the tool budget: the ones that have broken main past a selection; measured about 30 s plus the loop transcript's one long test). Left out of the default for their cost, add them when they bite: `tests/test_architecture_guards.py` (18 s), `tests/test_compat*.py`, `tests/test_uc_surf_registry_rule_transcript.py` (37 s): their input is the whole code base, so no import closure can know a change affects them. Recorded in the gate evidence as `always` (count, files) and `always_s` (the seconds they cost); when the closure is empty they run alone, in place of the whole-suite fallback (the guards are the recall for such a change). Empty list: only the selection runs.",
    )

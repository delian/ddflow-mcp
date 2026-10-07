"""The `[ci]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, knob

#: `[ci].on_merge`'s values.
CI_ON_MERGE_MODES = ("off", "fast", "full")


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
        doc="Seconds the CI command may run before the gate records UNAVAILABLE. Default 3600: the pre-push set includes the whole test suite.",
    )
    #: Health of the base after a merge lands: off | fast (lint, format, security: the
    #: pre-commit set without the test hooks) | full (the whole command).
    on_merge: str = knob(
        "fast",
        doc="Health check of the base right after a merge lands: off | fast (default: the project's pre-commit set without the test hooks, `SKIP=tests,scenarios`; an explicit [ci].command runs as written) | full (the whole command). The result is recorded as `ci.result`; each failing check files one bug and fix task while the bug is open. A project with no CI command is left alone.",
        choices=CI_ON_MERGE_MODES,
        strictest=("full", "the whole CI command runs after a merge"),
    )

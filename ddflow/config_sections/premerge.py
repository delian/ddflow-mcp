"""The `[premerge]` section: the project's own pre-commit hooks on the files an item changed,
run right before a merge (D-premerge-precommit-gate-2026-11-12).

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, int_at_least, knob


@declare("premerge")
@dataclass
class PremergeConfig:
    """The pre-merge check: the project's pre-commit hooks on the item's changed files, with
    the fixes they make committed, and the item's changed tests."""

    enabled: bool = knob(
        True,
        doc="Whether the pre-merge check runs. Only the operator turns it off: an agent's config write is refused. Off, merge behaves as it did before the check existed.",
        operator_only=True,
    )
    stages: list[str] = knob(
        factory=lambda: ["pre-commit"],
        doc="The pre-commit stages whose hooks run, each as `pre-commit run --files <changed> --hook-stage <stage>`. Add `pre-push` for the project's push-time hooks; their test-running hooks stay off (the item's own tests run through `test_command`).",
    )
    budget_s: int = knob(
        120,
        doc="Seconds the whole check may take. A step still running at the deadline is stopped and recorded `unavailable` (never passed), and the steps after it are not started.",
        check=int_at_least(1),
    )
    on_unavailable: str = knob(
        "unavailable",
        doc="What the check does when a hook cannot run (pre-commit or a hook's tool missing, a hook repository not cached and no network, the budget spent). 'unavailable' (default): that step is recorded unavailable, never passed, and the merge goes on. 'refuse': an unavailable step refuses like a failed one. A hook that ran and failed refuses in both modes, and a force flag never lifts it.",
        choices=("unavailable", "refuse"),
        strictest=("refuse", "a hook that could not run refuses the merge"),
    )
    fix: bool = knob(
        True,
        doc="Apply the fixes hooks make (formatting, import order, trailing whitespace) to the item's OWN changed files and commit them to the item's branch, listing the files. Off, a hook that would rewrite a file is reported as failed and the tree is left as it was. A file the item did not change is never touched, never committed.",
    )
    max_fix_rounds: int = knob(
        1,
        doc="How many times fixes are committed and the hooks re-run. A hook that still rewrites a file after the last round ends failed, naming the file.",
        check=int_at_least(0),
    )
    precommit_command: str = knob(
        "pre-commit",
        doc="The pre-commit executable. Not found: the hook step is recorded unavailable. ddflow never installs it.",
    )
    skip_hooks: list[str] = knob(
        factory=list,
        doc="Hook ids left out of the run (the SKIP of pre-commit), for a hook too slow or too wide for a merge. A skipped hook is listed as skipped, never as passed.",
    )
    generic_hooks: bool = knob(
        True,
        doc="When the project has no .pre-commit-config.yaml, run the built-in generic set on the changed files: trailing whitespace, final newline, merge-conflict markers, private-key header, oversized file, and ruff check and format for Python files. Off, such a project gets no hook step.",
    )
    large_file_kb: int = knob(
        500,
        doc="The size above which the generic set refuses a changed file, in kilobytes.",
        check=int_at_least(1),
    )
    test_command: str = knob(
        "python -m pytest -q -x",
        doc="The command run with the item's changed or added test files appended; never the whole suite. No such file: the step is skipped, not passed. A command that is not found records the step unavailable.",
    )

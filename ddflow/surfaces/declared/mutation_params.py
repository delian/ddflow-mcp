"""The mutation that shows a regression test guards a fix confined to test files.

Shared by `bug fixed` and `complete` (B Bc262739f62): three parameters on every surface,
read back as the one `{file, old, new}` dict the api takes.
"""

from __future__ import annotations

from typing import Any

from ..registry import Param

MUTATION_PARAMS: tuple[Param, ...] = (
    Param(
        "mutation_file",
        help="For a fix that lives in the TEST files themselves (no pre-fix tree fails the new test): a changed test file the mutation edits. ddflow applies the edit to a scratch copy of the fixed tree and the named test must FAIL; recorded as verified-by-mutation. Needs mutation_old.",
        cli_help="fix confined to test files: the changed test file the mutation edits (with --mutation-old/--mutation-new); the test must fail on it",
        default="",
    ),
    Param(
        "mutation_old",
        help="The exact text (appearing once in mutation_file) that disables the fix when replaced.",
        cli_help="the text in --mutation-file that --mutation-new replaces (must appear once)",
        default="",
    ),
    Param(
        "mutation_new",
        help="What mutation_old becomes: the fix disabled.",
        cli_help="the replacement that disables the fix",
        default="",
    ),
)

MUTATION_ORDER = ("mutation_file", "mutation_old", "mutation_new")


def mutation_of(a: dict[str, Any]) -> dict[str, str] | None:
    """The `{file, old, new}` the three parameters name, None when none was given."""
    file, old = a.get("mutation_file") or "", a.get("mutation_old") or ""
    if not file and not old:
        return None
    return {"file": file, "old": old, "new": a.get("mutation_new") or ""}

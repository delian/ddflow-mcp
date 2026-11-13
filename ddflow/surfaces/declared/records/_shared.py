"""Shared imports, constants and helpers of the declared record-keeping commands (split from records.py, r-small-files).

The command modules of this package declare one area's ``Command``s each; ``records/__init__.py``
re-exports the names the single module held."""

# ruff: noqa: F401  (the imports are re-exported for the command modules)

from __future__ import annotations

import importlib

from ....core import outcome as O
from ....core.outcome import OK
from ...registry import CliPolicy, Command, Param, by_tool
from ...tools._common import (
    _answer,
    _api,
    _bug_reopen,
    _bug_tag,
    _regression_tests,
    _reopening,
    _tagging,
)
from ..answer import ANSWER_FLAG_EXEMPT, ANSWER_PARAMS
from ..mutation_params import MUTATION_FLAG_EXEMPT, MUTATION_ORDER, MUTATION_PARAMS, mutation_of

#: The body of `bug fixed`, on both surfaces (the tag form adds its own fields below).
BUG_FIXED_FIELDS = (
    "id",
    "landed",
    "regression_verified",
    "regression_verify",
    "lesson_captured",
    "lesson_capture",
    "open_duplicates",
)

#: The body of `bug invalid`, on both surfaces (`ddflow bug invalid --json` and the tool).
BUG_INVALID_FIELDS = (
    "id",
    "invalid_reason",
    "evidence",
    "unchecked",
    "fix_task",
    "fix_task_removed",
    "fix_task_abandoned",
    "fix_task_kept",
    "fix_task_holder",
    "fix_task_tree",
)


def file_tasks_call(repo, a: dict, agent: str):
    """`bug file-tasks`: one bug's task (`id`), every bug's (`all`), else a preview."""
    bug = a.get("id") or ""
    if bug and a.get("all"):
        return O.failed("bug.file_tasks", "pass a bug id or --all/all, not both")
    return _api().bug_file_tasks(
        repo, bug_id=bug, every=bool(a.get("all")), dry_run=bool(a.get("dry_run")), agent=agent
    )


def _bug_dedupe_render(out, a):
    data = out.data
    if not data.get("clusters"):
        return ""  # the executor says why instead
    lines = []
    for c in data["clusters"]:
        dupes = ", ".join(f"{d['id']} ({d['score']:.2f})" for d in c["duplicates"]) or "(none)"
        cause = f"   shared: {c['cause']}" if c.get("cause") else ""
        lines.append(f"{c['canonical']} ~ {dupes}")
        if cause:
            lines.append(cause)
    if lines:
        lines.append(f"matcher: {data.get('semantic', '')}")
    applied = data.get("applied") or []
    if applied:
        lines.append(
            f"linked {', '.join(applied)} onto their canonical (nothing closed); "
            f"settle any left with `ddflow link <dup> --duplicate-of <canonical>`"
        )
    return "\n".join(lines)

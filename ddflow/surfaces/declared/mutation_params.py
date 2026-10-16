"""The mutation that shows a regression test guards a fix confined to test files.

Shared by `bug fixed` and `complete` (B Bc262739f62): three flags on the command line, one
JSON parameter over MCP (the tool list is size-budgeted), both read back as the one
`{file, old, new}` dict the api takes.
"""

from __future__ import annotations

import json
from typing import Any

from ..registry import Param

MUTATION_PARAMS: tuple[Param, ...] = (
    Param(
        "mutation_file",
        help="",
        cli_help="fix confined to test files: the changed test file the mutation edits (with --mutation-old/--mutation-new); ddflow applies it and the test must fail on it",
        default="",
        cli_only=True,
    ),
    Param(
        "mutation_old",
        help="",
        cli_help="the text in --mutation-file (once) that --mutation-new replaces",
        default="",
        cli_only=True,
    ),
    Param(
        "mutation_new",
        help="",
        cli_help="the replacement that disables the fix",
        default="",
        cli_only=True,
    ),
    Param(
        "mutation",
        help='Test-only fix: JSON {"file","old","new"} disabling it; the test must FAIL.',
        mcp_only=True,
    ),
)

MUTATION_ORDER = ("mutation",)


def mutation_of(a: dict[str, Any]) -> dict[str, str] | None:
    """The `{file, old, new}` the parameters name, None when none was given."""
    raw = a.get("mutation")
    if raw:
        try:
            got = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except (ValueError, TypeError):
            return {"file": "", "old": "", "new": ""}  # refused downstream: names no `old`
        return {k: str(got.get(k) or "") for k in ("file", "old", "new")}
    file, old = a.get("mutation_file") or "", a.get("mutation_old") or ""
    if not file and not old:
        return None
    return {"file": file, "old": old, "new": a.get("mutation_new") or ""}

"""Grade one model answer against a scenario; tally a run."""

from __future__ import annotations

import json
from typing import Any, NamedTuple

from td_scenarios import Scenario
from td_surfaces import resolve


class Graded(NamedTuple):
    called: bool
    valid: bool  # the call resolved to a member and passed the schema/required check
    tool_ok: bool
    args_ok: bool


def grade(scenario: Scenario, surface: str, name: str, raw_args: Any) -> Graded:
    """``name`` and ``raw_args`` (a dict or its JSON text) as the model emitted them."""
    if not name:
        return Graded(False, False, False, False)
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
    except (ValueError, TypeError):
        return Graded(True, False, False, False)
    if not isinstance(args, dict):
        return Graded(True, False, False, False)
    member, member_args, error = resolve(surface, name, args)
    text = json.dumps(member_args, default=str)
    return Graded(True, not error, member == scenario.tool, all(v in text for v in scenario.values))


def tally(rows: list[Graded]) -> dict[str, float]:
    n = len(rows) or 1
    return {
        "calls": len(rows),
        "no_call": sum(not r.called for r in rows),
        "invalid": sum(r.called and not r.valid for r in rows),
        "wrong_tool": sum(r.called and not r.tool_ok for r in rows),
        "wrong_args": sum(r.tool_ok and not r.args_ok for r in rows),
        "correct": sum(r.tool_ok and r.args_ok and r.valid for r in rows),
        "accuracy": round(sum(r.tool_ok and r.args_ok and r.valid for r in rows) / n, 3),
    }

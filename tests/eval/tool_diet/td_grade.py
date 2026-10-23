"""Grade one model answer against a scenario; tally a run."""

from __future__ import annotations

import json
import re
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
    leaves = list(_leaves(member_args))
    return Graded(
        True, not error, member == scenario.tool, all(_carried(v, leaves) for v in scenario.values)
    )


def _leaves(value: Any):
    """Every string a call carries, however nested."""
    if isinstance(value, dict):
        for v in value.values():
            yield from _leaves(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _leaves(v)
    elif value is not None:
        yield str(value)


def _carried(wanted: str, leaves: list[str]) -> bool:
    """``wanted`` stands as a whole token in some string (B-1 is not in B-10)."""
    flags = 0 if any(c.isdigit() for c in wanted) else re.IGNORECASE  # ids are exact
    pattern = re.compile(rf"(?<![\w-]){re.escape(wanted)}(?![\w-])", flags)
    return any(pattern.search(leaf) for leaf in leaves)


def tally(rows: list[Graded]) -> dict[str, float]:
    """Disjoint buckets: ``invalid`` (no usable call: unparseable or refused by the server),
    ``wrong_tool`` (a valid call to another member), ``wrong_args`` (the right member, other
    values), ``correct``; ``no_call`` is a request with no tool call at all."""
    n = len(rows) or 1
    valid = [r for r in rows if r.called and r.valid]
    correct = sum(r.tool_ok and r.args_ok for r in valid)
    return {
        "requests": len(rows),
        "calls": sum(r.called for r in rows),
        "no_call": sum(not r.called for r in rows),
        "invalid": sum(r.called and not r.valid for r in rows),
        "wrong_tool": sum(not r.tool_ok for r in valid),
        "wrong_args": sum(r.tool_ok and not r.args_ok for r in valid),
        "correct": correct,
        "accuracy": round(correct / n, 3),
    }

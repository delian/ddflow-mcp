"""Named output parsers for the tools in `tools.toml`: text in, normalized `Finding`s out.

A parser takes ``(tool_id, output)`` and returns findings, or raises `ToolParseError` for output
that is not in the shape it reads -- which `ExternalTool.run` turns into `unavailable`, never
into "nothing found". Add one to `PARSERS` below and name it in a table row.

A parser never copies a secret: gitleaks reports carry the matched text, and only its rule,
file and line are kept here (the argv in the table also asks for ``--redact``).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from .registry import Registry
from .tool_types import Finding, ToolParseError

Parser = Callable[[str, str], list[Finding]]

PARSERS: Registry[Parser] = Registry("tool parser")


def _int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _json_list(output: str, what: str) -> list[Any]:
    """The first JSON array in ``output``: a tool may log a line or two before its report, so
    each line start is tried. Empty output is an empty report; anything else is an error."""
    text = output.strip()
    if not text:
        return []
    starts = [m.start() for m in re.finditer(r"(?m)^\[", text)]
    for at in starts:
        try:
            value, _ = json.JSONDecoder().raw_decode(text[at:])
        except ValueError:
            continue
        if isinstance(value, list):
            return value
    raise ToolParseError(
        f"{what}: no JSON array in the output (first 80 characters: {text[:80]!r})"
    )


def parse_none(tool: str, output: str) -> list[Finding]:
    """No structured findings: the exit code is the whole answer."""
    return []


def parse_normalized(tool: str, output: str) -> list[Finding]:
    """A JSON array already in the normalized shape (what the fake emits, and what a wrapper
    script can print): each row needs a ``rule``."""
    rows = _json_list(output, tool)
    out = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("rule"):
            raise ToolParseError(f"{tool}: a finding without a 'rule': {str(row)[:80]}")
        out.append(
            Finding(
                tool,
                str(row["rule"]),
                str(row.get("severity", "")),
                str(row.get("file", "")),
                _int(row.get("line", 0)),
                str(row.get("advisory", "")),
            )
        )
    return out


def parse_gitleaks(tool: str, output: str) -> list[Finding]:
    """`gitleaks detect --report-format json`: a leak has no severity of its own, so each is
    ``high``; the secret and the matched text are dropped."""
    out = []
    for row in _json_list(output, tool):
        if not isinstance(row, dict) or not row.get("RuleID"):
            raise ToolParseError(f"{tool}: a report row without a RuleID: {str(row)[:80]}")
        out.append(
            Finding(
                tool,
                str(row["RuleID"]),
                "high",
                str(row.get("File", "")),
                _int(row.get("StartLine", 0)),
            )
        )
    return out


#: A pre-commit result line: ``<hook name>....Failed`` (the dots pad to the terminal width).
_HOOK_LINE = re.compile(r"^(?P<hook>.+?)\.{3,}(?:\(.*\))?\s*(?P<state>Passed|Failed|Skipped)\s*$")


def parse_precommit(tool: str, output: str) -> list[Finding]:
    """`pre-commit run` (and `prek run`): each hook that printed ``Failed`` is one finding with
    the hook's name as the rule; the file and line are the hook's own business (not reported)."""
    return [
        Finding(tool, m["hook"].strip(), "error")
        for line in output.splitlines()
        if (m := _HOOK_LINE.match(line.strip())) and m["state"] == "Failed"
    ]


for _name, _fn in (
    ("none", parse_none),
    ("normalized", parse_normalized),
    ("gitleaks", parse_gitleaks),
    ("pre-commit", parse_precommit),
):
    PARSERS.register(_fn, _name)

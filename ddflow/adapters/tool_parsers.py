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


def _text(value: Any) -> str:
    """A JSON field as text: null is absent, not the string "None"."""
    return "" if value is None else str(value)


def _int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):  # OverflowError: JSON Infinity / 1e309
        return 0


def _json_list(output: str, what: str) -> list[Any]:
    """The last top-level JSON array in ``output``: a tool may log a line or two before its report, so
    each line start is tried. Empty output is an empty report; anything else is an error."""
    text = output.strip()
    if not text:
        return []
    starts = [m.start(1) for m in re.finditer(r"(?m)^[ \t]*(\[)", text)]
    found: list[list[Any]] = []
    covered = 0  # arrays nested inside one already read are not candidates
    for at in starts:
        if at < covered:
            continue
        try:
            value, end = json.JSONDecoder().raw_decode(text[at:])
        except (ValueError, RecursionError):
            continue
        if isinstance(value, list):
            found.append(value)
            covered = at + end
    if found:
        return found[-1]  # a wrapper's log line may come first; the report is the last array
    # The output is not echoed: a scanner's text can hold the secret it found.
    raise ToolParseError(f"{what}: no JSON array in the output ({len(text)} characters)")


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
            raise ToolParseError(f"{tool}: a finding without a 'rule'")
        out.append(
            Finding(
                tool,
                str(row["rule"]),
                _text(row.get("severity")),
                _text(row.get("file")),
                _int(row.get("line", 0)),
                _text(row.get("advisory")),
            )
        )
    return out


def parse_gitleaks(tool: str, output: str) -> list[Finding]:
    """`gitleaks detect --report-format json`: a leak has no severity of its own, so each is
    ``high``; the secret and the matched text are dropped."""
    out = []
    for row in _json_list(output, tool):
        if not isinstance(row, dict) or not row.get("RuleID"):
            raise ToolParseError(f"{tool}: a report row without a RuleID")
        out.append(
            Finding(
                tool,
                str(row["RuleID"]),
                "high",
                _text(row.get("File")),
                _int(row.get("StartLine", 0)),
            )
        )
    return out


#: A pre-commit result line: ``<hook name>....Failed`` (the dots pad to the terminal width;
#: a very long hook name leaves only two of them, so no `{3,}` floor).
_HOOK_LINE = re.compile(
    r"^(?P<hook>[A-Za-z0-9_(\[].*?)\.{2,}(?:\(.*?\))?\s*(?P<state>Passed|Failed|Skipped)\s*(?:\(.*\))?$"
)


def parse_precommit(tool: str, output: str) -> list[Finding]:
    """`pre-commit run` (and `prek run`): each hook that printed ``Failed`` is one finding with
    the hook's name as the rule; the file and line are the hook's own business (not reported)."""
    return [
        Finding(tool, m["hook"].strip(), "error")
        for line in output.splitlines()
        if (m := _HOOK_LINE.match(line.rstrip())) and m["state"] == "Failed"
    ]


for _name, _fn in (
    ("none", parse_none),
    ("normalized", parse_normalized),
    ("gitleaks", parse_gitleaks),
    ("pre-commit", parse_precommit),
):
    PARSERS.register(_fn, _name)

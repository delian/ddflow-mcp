"""The one output line that carries an item's `ci run` scope to the gate that records it."""

from __future__ import annotations

import json
import re

#: Printed by `ddflow ci run`, so a gate that only sees the output can record the scope.
MARKER = "ddflow-ci-scope "
_MARKER_LINE = re.compile(rf"^{MARKER}(\{{.*\}})\s*$", re.M)


def marker_line(evidence: dict) -> str:
    """The one output line that carries a run's scope to the gate that records it."""
    return MARKER + json.dumps(evidence, separators=(",", ":"))


def parse_marker(output: str) -> dict:
    """The scope a `ci run` printed (the last marker line of ``output``), or {}."""
    found = _MARKER_LINE.findall(output or "")
    try:
        data = json.loads(found[-1]) if found else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}

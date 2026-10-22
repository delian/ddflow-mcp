"""The one output line that carries an item's `ci run` scope to the gate that records it."""

from __future__ import annotations

import json
import re

#: Printed by `ddflow ci run`, so a gate that only sees the output can record the scope.
MARKER = "ddflow-ci-scope "
_MARKER_LINE = re.compile(rf"^{MARKER}(\{{.*\}})\s*$", re.M)


#: Printed by `ddflow ci run` after it waited for the host suite lock (B1d51fb9da1): the
#: seconds it queued, which the gate that ran it records as evidence ``waited_s``.
WAIT_MARKER = "ddflow-suite-wait "
_WAIT_LINE = re.compile(rf"^{WAIT_MARKER}([0-9]+(?:\.[0-9]+)?)\s*$", re.M)


def wait_line(waited_s: float) -> str:
    return f"{WAIT_MARKER}{waited_s:.1f}"  # fixed notation: `parse_wait` reads no exponent


def parse_wait(output: str) -> float | None:
    """The seconds a `ci run` queued for the suite lock (its last wait line), or None."""
    found = _WAIT_LINE.findall(output or "")
    return float(found[-1]) if found else None


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

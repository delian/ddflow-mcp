"""`rule inventory`: the read-only census of every agent's native rule and command files."""

from __future__ import annotations

from pathlib import Path

from ..core import outcome as O
from ..services import rulesinv as RI


def rule_inventory(repo: Path, *, agent: str = "") -> O.Outcome:
    """Who loads which rule file, when, and at what always-loaded cost. Writes nothing. ``agent`` is
    the caller identity every surface passes to its api function; it is neither recorded nor a
    filter here: every agent is in the report."""
    try:
        return O.ok("rule.inventory", **RI.inventory(repo))
    except OSError as exc:
        return O.failed("rule.inventory", f"could not read the rule files: {exc}")

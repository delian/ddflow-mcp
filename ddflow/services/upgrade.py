"""What the log says about ddflow versions: the stamp, skew, and the overrides to review.

The pieces `ddflow doctor` and (later) `ddflow upgrade` share. Pure over a folded `State`:
nothing here reads a disk except `last_seen_version`, which reads the machine-local marker.

The mechanism lives lower down: `core.events` (the kinds, version order, `stamp_facts`) and
`infra.log.EventLog` (the stamp on first write, the guard, the override). Decisions
D-upgrade-event-kinds and D-upgrade-skew-guard.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..core.events import is_older
from ..core.model import State
from ..infra.log import SEEN_MARKER, running_version


def last_seen_version(root: Path | str) -> str:
    """The version this MACHINE last acted under (`.ddflow/local/seen.json`), "" when it
    has never written to this project."""
    try:
        data = json.loads((Path(root) / SEEN_MARKER).read_text("utf-8"))
    except (OSError, ValueError):
        return ""
    return str(data.get("version", "")) if isinstance(data, dict) else ""


def skew_report(st: State, running: str = "") -> dict[str, Any]:
    """The version facts of a folded log, as plain data.

    `skew` is True when the running ddflow is OLDER than the highest stamp; `overrides` and
    `older_events` are what a later upgrade should review: every skew override and how many
    events were written by an older ddflow because of one."""
    running = running or running_version()
    highest = st.highest_version
    return {
        "running": running,
        "highest": highest,
        "skew": bool(highest) and is_older(running, highest),
        "versions": {v: dict(r) for v, r in st.ddflow_versions.items()},
        "overrides": [dict(o) for o in st.skew_overrides],
        "older_events": dict(st.older_version_events),
        "upgrades": [dict(u) for u in st.upgrades],
    }


def skipped_kinds_advice(st: State) -> str:
    """The remedy for events this code skipped: they come from a NEWER ddflow, so the fix
    is to upgrade this one -- not to "merge main", which was advice for a source checkout."""
    kinds = ", ".join(f"{k} x{n}" for k, n in sorted(st.skipped_kinds.items()))
    highest = st.highest_version
    target = f" >= {highest}" if highest else ""
    return (
        f"this log has events from a newer ddflow than this one ({running_version()}), "
        f"skipped: {kinds}. Every number computed here is WITHOUT them. Upgrade "
        f"ddflow-mcp{' to' + target if target else ''} "
        f"(restart the MCP server after upgrading)"
    )


def doctor_notes(st: State, running: str = "") -> list[str]:
    """Lines for `ddflow doctor`: a skew (this ddflow is older than the log's highest stamp),
    the overrides and the events an older ddflow wrote under them. Nothing on a healthy log:
    the version itself is `ddflow status --json` (`ddflow_version`)."""
    rep = skew_report(st, running)
    notes: list[str] = []
    if rep["skew"]:
        notes.append(
            f"ddflow {rep['running']} is OLDER than this log's highest stamp "
            f"({rep['highest']}): writes are refused unless [upgrade].skew allows them. "
            f"Upgrade ddflow-mcp to >= {rep['highest']}"
        )
    for o in rep["overrides"]:
        notes.append(
            f"skew override: {o['agent']} wrote with ddflow {o['running']} to a "
            f"{o['log_version']} log ({o['at'][:16]}): {o['reason']}"
        )
    for version, n in sorted(rep["older_events"].items()):
        notes.append(
            f"{n} event(s) were written by an older ddflow ({version}) under a skew override; "
            f"review them after upgrading"
        )
    return notes

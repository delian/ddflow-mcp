"""Where a lane's conformance level lives: one JSON file the scheduler (SW-launch) reads.

Machine-local (a lane's level is a fact about this machine's agent install), so it sits in the
git-ignored ``.ddflow/local/`` layer. Writes are atomic; a missing or unreadable file reads as
"no lane has a level", which admits nothing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .conformance import Report

SCHEMA = "lane-levels@1"
RELATIVE = Path(".ddflow") / "local" / "lane-levels.json"


def path_for(root: Path) -> Path:
    return Path(root) / RELATIVE


def load_all(root: Path) -> dict[str, Report]:
    try:
        raw = json.loads(path_for(root).read_text(encoding="utf-8"))
        if raw.get("schema") != SCHEMA:
            return {}
        return {name: Report.from_data(d) for name, d in raw["lanes"].items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}


def load(root: Path, lane: str) -> Report | None:
    return load_all(root).get(lane)


def save(root: Path, report: Report) -> Path:
    """Record ``report`` as its lane's current level (replacing the lane's earlier one)."""
    lanes = load_all(root)
    lanes[report.lane] = report
    p = path_for(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
    body = {"schema": SCHEMA, "lanes": {k: v.to_data() for k, v in sorted(lanes.items())}}
    tmp.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return p

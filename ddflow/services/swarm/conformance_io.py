"""Where a lane's conformance level lives: one JSON file the scheduler (SW-launch) reads.

Machine-local (a lane's level is a fact about this machine's agent install), so it sits in the
git-ignored ``.ddflow/local/`` layer. Writes take the file lock and are atomic; a missing or
unreadable file reads as "no lane has a level", which admits nothing. A file of a schema this
code does not know is never overwritten (``save`` refuses).
"""

from __future__ import annotations

import json
from pathlib import Path

from ...infra import fsio
from .conformance import Report

SCHEMA = "lane-levels@1"
RELATIVE = Path(".ddflow") / "local" / "lane-levels.json"


def path_for(root: Path) -> Path:
    return Path(root) / RELATIVE


def _read(p: Path) -> dict:
    """The raw document, ``{}`` if absent or unreadable."""
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def _reports(raw: dict) -> dict[str, Report]:
    """The readable lanes of a document of this schema; a malformed entry is skipped."""
    out: dict[str, Report] = {}
    lanes = raw.get("lanes") if raw.get("schema") == SCHEMA else None
    for name, d in lanes.items() if isinstance(lanes, dict) else ():
        try:
            out[name] = Report.from_data(d)
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return out


def load_all(root: Path) -> dict[str, Report]:
    return _reports(_read(path_for(root)))


def load(root: Path, lane: str) -> Report | None:
    return load_all(root).get(lane)


def save(root: Path, report: Report) -> Path:
    """Record ``report`` as its lane's current level (replacing the lane's earlier one).
    Raises ValueError rather than overwrite a file written under another schema."""
    p = path_for(root)
    with fsio.file_lock(fsio.lock_path_for(p)):
        raw = _read(p)
        if raw and raw.get("schema") != SCHEMA:
            raise ValueError(
                f"{p} has schema {raw.get('schema')!r}, not {SCHEMA!r}; not overwritten"
            )
        old = raw.get("lanes")
        lanes = (
            dict(old) if isinstance(old, dict) else {}
        )  # kept as written: a malformed entry survives
        lanes[report.lane] = report.to_data()
        body = {"schema": SCHEMA, "lanes": dict(sorted(lanes.items()))}
        p.parent.mkdir(parents=True, exist_ok=True)
        fsio.atomic_write(p, json.dumps(body, indent=2, sort_keys=True) + "\n")
    return p

"""The hook's read of the lease snapshot: stdlib only, one small file, no log fold.

The per-edit write hook (`swarm.lanehooks_check`) used to fold the whole event log to learn
which files its lane may write: 3.7 s on a 108 MB log. `services.leases.snapshot` keeps
`.ddflow/local/lease-snapshot.json` current on every lease event; this module is the cheap
reading side. It imports nothing from ddflow so the hook pays no import cost for it.

A snapshot that cannot be trusted -- missing, torn, from a newer format, or older than
`[locks].snapshot_max_age_s` -- is reported as such with a sentence naming the next call,
never read as "no leases". The caller FAILS OPEN to the commit-time check and prints that
sentence (a silent pass is the bug this exists to avoid).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The file under `.ddflow/local`, and the one number the file format is versioned by.
NAME = "lease-snapshot.json"
SCHEMA = 1
OK, MISSING, UNREADABLE, STALE, NEWER = "ok", "missing", "unreadable", "stale", "newer"

REFRESH = (
    "any lease call rewrites it: `ddflow heartbeat ITEM` (MCP `ddflow_heartbeat`); until then "
    "the commit-time lease check (`ddflow check-commit`) is the only check"
)


@dataclass(frozen=True)
class Snapshot:
    """What a hook may rely on. ``status`` is OK only when ``leases`` is a trustworthy picture."""

    status: str
    note: str = ""
    generation: int = 0
    leases: dict[str, dict[str, Any]] = field(default_factory=dict)
    shared: list[str] = field(default_factory=list)
    new_files: list[str] = field(default_factory=list)
    grace_s: int = 0

    def live_for(self, agent: str, now: float) -> list[dict[str, Any]]:
        """The leases ``agent`` holds that are inside their TTL (plus the recorded grace)."""
        return [
            lz
            for lz in self.leases.values()
            if lz.get("holder") == agent
            and now - float(lz.get("renewed_at", 0)) <= int(lz.get("ttl_s", 0)) + self.grace_s
        ]


def path_of(repo: Path | str) -> Path:
    return Path(repo) / ".ddflow" / "local" / NAME


def _bad(status: str, why: str) -> Snapshot:
    return Snapshot(status, f"ddflow lease snapshot {why}; {REFRESH}.")


def read(repo: Path | str, *, now: float | None = None, max_age_s: int | None = None) -> Snapshot:
    """The snapshot of ``repo``. ``max_age_s`` overrides the age limit the writer recorded."""
    p = path_of(repo)
    try:
        raw = json.loads(p.read_text("utf-8"))
    except FileNotFoundError:
        return _bad(MISSING, f"is missing ({p})")
    except (OSError, ValueError):
        return _bad(UNREADABLE, f"is unreadable ({p})")
    data = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(data, dict) or not isinstance(data.get("leases"), dict):
        return _bad(UNREADABLE, f"has no lease table ({p})")
    if not isinstance(raw.get("schema"), int) or raw["schema"] > SCHEMA:
        return _bad(NEWER, "was written by a newer ddflow")
    try:
        limit = int(data.get("max_age_s", 0) if max_age_s is None else max_age_s)
        age = (time.time() if now is None else now) - float(data["written_at"])
        gen = int(data.get("generation", 0))
        grace = int(data.get("grace_s", 0))
    except (TypeError, ValueError, KeyError):
        return _bad(UNREADABLE, f"has bad numbers ({p})")
    if limit > 0 and age > limit:
        return _bad(STALE, f"is {int(age)} s old (limit {limit} s, [locks].snapshot_max_age_s)")
    return Snapshot(
        OK,
        generation=gen,
        leases={str(k): v for k, v in data["leases"].items() if isinstance(v, dict)},
        shared=[str(g) for g in data.get("shared", [])],
        new_files=[str(g) for g in data.get("new_files", [])],
        grace_s=grace,
    )

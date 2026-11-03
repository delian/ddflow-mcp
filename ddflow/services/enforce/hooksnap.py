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
import math
import os
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


#: The files whose change makes a snapshot's shared globs and age limit untrustworthy.
CONFIG_FILES = (Path(".ddflow") / "config.toml", Path(".ddflow") / "local" / "config.toml")


def config_stamp(repo: Path | str) -> list[int]:
    """Modification time (ns) of each config file, 0 when absent: the writer records it and the
    reader compares, so a config edit after the snapshot was written cannot leave a stale
    `shared` list in force."""
    out = []
    for rel in CONFIG_FILES:
        try:
            out.append(os.stat(Path(repo) / rel).st_mtime_ns)
        except OSError:
            out.append(0)
    return out


def _bad(status: str, why: str) -> Snapshot:
    return Snapshot(status, f"ddflow lease snapshot {why}; {REFRESH}.")


def _num(v: Any) -> float:
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
        raise ValueError(v)
    return float(v)


def _globs(v: Any) -> list[str]:
    if not isinstance(v, list) or not all(isinstance(g, str) for g in v):
        raise ValueError(v)
    return list(v)


def _lease(v: Any) -> dict[str, Any]:
    if not isinstance(v, dict) or not isinstance(v.get("holder"), str):
        raise ValueError(v)
    return {
        "holder": v["holder"],
        "globs": _globs(v.get("globs", [])),
        "renewed_at": _num(v["renewed_at"]),
        "ttl_s": _num(v["ttl_s"]),
    }


def read(repo: Path | str, *, now: float | None = None, max_age_s: int | None = None) -> Snapshot:
    """The snapshot of ``repo``. ``max_age_s`` overrides the age limit the writer recorded.
    Any malformed part makes the whole snapshot UNREADABLE: never a partial picture."""
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
        limit = int(_num(data.get("max_age_s", 0) if max_age_s is None else max_age_s))
        age = (time.time() if now is None else now) - _num(data["written_at"])
        gen = int(_num(data.get("generation", 0)))
        grace = int(_num(data.get("grace_s", 0)))
        leases = {str(k): _lease(v) for k, v in data["leases"].items()}
        shared, new_files = _globs(data.get("shared", [])), _globs(data.get("new_files", []))
        stamp = data.get("config_stamp")
    except (TypeError, ValueError, KeyError, OverflowError):
        return _bad(UNREADABLE, f"has malformed content ({p})")
    if stamp != config_stamp(repo):
        return _bad(STALE, "predates a change of the ddflow config (its shared globs may differ)")
    if limit > 0 and age > limit:
        return _bad(STALE, f"is {int(age)} s old (limit {limit} s, [locks].snapshot_max_age_s)")
    return Snapshot(
        OK,
        generation=gen,
        leases=leases,
        shared=shared,
        new_files=new_files,
        grace_s=grace,
    )

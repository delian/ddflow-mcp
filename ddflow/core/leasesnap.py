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
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..adapters.clock import DEFAULT as CLOCK
from .digest import hasher

#: The file under `.ddflow/local`, and the one number the file format is versioned by.
NAME = "lease-snapshot.json"
DOC_SCHEMA = 1
OK, MISSING, UNREADABLE, STALE, NEWER = "ok", "missing", "unreadable", "stale", "newer"

#: How far ahead of this clock a snapshot's write time may be (clocks of one machine differ little).
MAX_SKEW_S = 60

REFRESH = (
    "any lease call rewrites it: `ddflow heartbeat ITEM` (MCP `ddflow_heartbeat`); until then "
    "the commit-time lease check (`ddflow precommit`) is the only check"
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


#: A config file larger than this is not read by the hook (it would blow the time budget).
MAX_CONFIG_BYTES = 1 << 20
UNREADABLE_CONFIG = "unreadable"


def config_stamp(repo: Path | str) -> list[str]:
    """A digest of each config file's CONTENT ("absent" when it is not there, "unreadable" when it
    is there but is not a plain readable file of sane size): the writer records it and the reader
    compares, so a config edit after the snapshot was written cannot leave a stale `shared` list
    in force, whatever it did to the file's size or mtime."""
    out = []
    for rel in CONFIG_FILES:
        p = Path(repo) / rel
        try:
            st = os.stat(p)
            if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_CONFIG_BYTES:
                out.append(UNREADABLE_CONFIG)
            else:
                out.append(hasher(p.read_bytes()).hexdigest())
        except FileNotFoundError:
            out.append("absent")
        except OSError:
            out.append(UNREADABLE_CONFIG)
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
    out = {
        "holder": v["holder"],
        "globs": _globs(v.get("globs", [])),
        "renewed_at": _num(v["renewed_at"]),
        "ttl_s": _num(v["ttl_s"]),
    }
    if out["renewed_at"] < 0 or out["ttl_s"] < 0:
        raise ValueError(v)
    return out


def _stamp(stamp: Any) -> list[str]:
    if not (isinstance(stamp, list) and all(isinstance(x, str) for x in stamp)):
        raise ValueError(stamp)
    if len(stamp) != len(CONFIG_FILES):  # one entry per config file; a wrong length is malformed
        raise ValueError(stamp)
    return stamp


def _leases(table: Any, now: float) -> dict[str, dict[str, Any]]:
    if not isinstance(table, dict):
        raise ValueError(table)
    out = {str(k): _lease(v) for k, v in table.items()}
    if any(lz["renewed_at"] > now + MAX_SKEW_S for lz in out.values()):
        raise ValueError("renewed in the future")
    return out


def _fields(data: dict[str, Any], now: float | None, max_age_s: int | None) -> tuple:
    """Every field of the document, validated; raises on any malformed part."""
    limit = int(_num(data.get("max_age_s", 0) if max_age_s is None else max_age_s))
    t = CLOCK.wall() if now is None else now
    age = t - _num(data["written_at"])
    if age < -MAX_SKEW_S:
        raise ValueError(age)  # written in the future: a rolled-back clock or a corrupt file
    return (
        limit,
        age,
        int(_num(data.get("generation", 0))),
        int(_num(data.get("grace_s", 0))),
        _leases(data["leases"], t),
        _globs(data.get("shared", [])),
        _globs(data.get("new_files", [])),
        _stamp(data.get("config_stamp")),
    )


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
    if not isinstance(raw.get("schema"), int) or raw["schema"] > DOC_SCHEMA:
        return _bad(NEWER, "was written by a newer ddflow")
    try:
        limit, age, gen, grace, leases, shared, new_files, stamp = _fields(data, now, max_age_s)
    except (TypeError, ValueError, KeyError, OverflowError, AttributeError):
        return _bad(UNREADABLE, f"has malformed content ({p})")
    live = config_stamp(repo)
    if UNREADABLE_CONFIG in live:
        return _bad(
            STALE, "cannot be checked against the ddflow config (a config file is unreadable)"
        )
    if stamp != live:
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

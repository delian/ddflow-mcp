"""Builders for the quota meter tests: a context on throwaway paths and real-shape sources."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ddflow.services import meters_base as MB

NOW = 2_000_000_000.0  # a fixed clock, so staleness is exact
HOUR = 3600.0


def ctx(tmp: Path, **over) -> MB.MeterContext:
    base = {
        "now": NOW,
        "stale_after_s": 900,
        "claude_state": tmp / "claude.json",
        "claude_projects": tmp / "projects",
        "kilo_db": tmp / "kilo.db",
        "snapshot": tmp / "state" / "vendor.json",
        "samples": tmp / "state" / "samples.jsonl",
        "windows": {"5h": NOW - 5 * HOUR, "week": NOW - 168 * HOUR},
        "token_fields": ("input", "output", "cache_read", "cache_write"),
        "transcript_max_bytes": 1 << 20,
        "kilo_timeout_s": 5.0,
        "samples_max_bytes": 1 << 20,
        "sample_interval_s": 300,
        "sample_tail_bytes": 8192,
        "events": None,
    }
    return MB.MeterContext(**{**base, **over})


def iso(t: float) -> str:
    return MB.iso_of(t)


def write_cache(path: Path, *, age_s: float = 10, five=None, week=None, **top) -> None:
    body = {
        "fetchedAtMs": (NOW - age_s) * 1000,
        "accountUuid": "11111111-2222-3333-4444-555555555555",
        "utilization": {"five_hour": five, "seven_day": week},
        **top,
    }
    path.write_text(json.dumps({"cachedUsageUtilization": body}))


def window(percent, resets_in_s: float = HOUR) -> dict:
    return {"utilization": percent, "resets_at": iso(NOW + resets_in_s)}


def turn(
    mid: str = "m1",
    rid: str = "r1",
    at: float = NOW - 60,
    inp: int = 10,
    out: int = 5,
    read: int = 100,
    write: int = 1,
) -> dict:
    return {
        "type": "assistant",
        "requestId": rid,
        "timestamp": iso(at),
        "message": {
            "id": mid,
            "model": "m",
            "usage": {
                "input_tokens": inp,
                "output_tokens": out,
                "cache_read_input_tokens": read,
                "cache_creation_input_tokens": write,
            },
        },
    }


def write_transcript(path: Path, rows: list, mtime: float = NOW) -> Path:
    import os

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in rows))
    os.utime(path, (mtime, mtime))
    return path


def kilo_db(path: Path, messages: list[dict], *, updated: float = NOW) -> Path:
    """A store with Kilo's own shape. Each message: at (epoch s), tokens, cost, role."""
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE session (id text PRIMARY KEY, time_updated integer NOT NULL, "
        "cost real DEFAULT 0 NOT NULL, tokens_input integer DEFAULT 0 NOT NULL)"
    )
    con.execute(
        "CREATE TABLE message (id text PRIMARY KEY, session_id text NOT NULL, "
        "time_created integer NOT NULL, data text NOT NULL)"
    )
    # A lifetime total on the session row: the reader must not take it for a window's use.
    con.execute("INSERT INTO session VALUES ('s1', ?, 0, 999999999)", (int(updated * 1000),))
    for n, m in enumerate(messages):
        data = {"role": m.get("role", "assistant"), "cost": m.get("cost", 0)}
        if "tokens" in m:
            data["tokens"] = m["tokens"]
        con.execute(
            "INSERT INTO message VALUES (?, 's1', ?, ?)",
            (f"m{n}", int(m["at"] * 1000), json.dumps(data)),
        )
    con.commit()
    con.close()
    return path


def kilo_tokens(inp=0, out=0, reasoning=0, read=0, write=0) -> dict:
    return {
        "input": inp,
        "output": out,
        "reasoning": reasoning,
        "cache": {"read": read, "write": write},
    }


def none_of(readings) -> bool:
    """Every reading has no figure, and there IS at least one: an empty list is not a pass."""
    got = list(readings)
    return bool(got) and all(r.status == MB.UNAVAILABLE and r.value is None for r in got)


def of(readings, window_: str, unit: str = ""):
    """The one reading of ``window_`` (and ``unit``, when given)."""
    got = [r for r in readings if r.window == window_ and (not unit or r.unit == unit)]
    assert len(got) == 1, got
    return got[0]

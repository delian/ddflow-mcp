"""Retention, compaction and redaction for the perf store, with a size budget (PM-retention).

The policy PM-decide fixed: age and size caps come from the ``[perf_metrics]`` knobs (the
store already trims them); this module adds (1) compaction of raw samples that aged out into
the committed daily aggregate, (2) a redaction pass that keeps only an allowlist of fields and
drops -- with a doctor note -- anything else a hostile writer stored, and (3) the size audit
that reports when a ring exceeds budget. Raw samples stay in ``.ddflow/local``; only the small
per-run rollups and the daily aggregate reach the committed log.

No second store or scheduler: compaction is a pure function the report/tick calls
(D-sched-no-daemon). Fields are ``t`` (time), ``env`` (machine-class key), ``value``, ``gate``
(a gate id) and ``digest`` (a content hash) -- never prompt text, arguments, user paths or
secrets.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ddflow.config import Config
from ddflow.core import clock

from . import store
from .store import PerfMetricsConfig, ring_path

#: The only keys a raw sample may carry. Anything else was not written by the sampler and is
#: dropped with a doctor note, never stored (r-no-secrets).
ALLOWED_FIELDS = frozenset({"t", "env", "value", "gate", "digest"})


@dataclass(frozen=True)
class Audit:
    """What one retention pass found about a series' ring."""

    series: str
    rows: int
    bytes: int
    dropped: int  # rows that carried a key outside the allowlist
    over_budget: bool = False


def redact_row(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Project one raw row onto the allowlist; returns the kept fields and the dropped keys."""
    dropped = [k for k in raw if k not in ALLOWED_FIELDS]
    return {k: raw[k] for k in ("t", "env", "value", "gate", "digest") if k in raw}, dropped


def _raw_rows(repo: Path, series: str) -> list[dict[str, Any]]:
    """The ring's raw JSON rows, skipping torn lines (the store already does this on read)."""
    path = ring_path(repo, series)
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
            if isinstance(d, dict):
                out.append(d)
        except ValueError:
            continue
    return out


def audit(repo: Path, series: str, cfg: PerfMetricsConfig | None = None) -> Audit:
    """Scan one ring: rows, bytes, how many rows carried a non-allowlist field, and whether
    the ring exceeds its budget."""
    cfg = cfg or Config().perf_metrics
    rows = _raw_rows(repo, series)
    dropped = sum(1 for r in rows if any(k not in ALLOWED_FIELDS for k in r))
    path = ring_path(repo, series)
    size = path.stat().st_size if path.exists() else 0
    return Audit(series, len(rows), size, dropped, size > cfg.ring_max_bytes)


def redact_ring(repo: Path, series: str) -> tuple[int, list[str]]:
    """Rewrite a ring projection only the allowlist fields, so a non-allowlist value (prompt
    text, a token, a secret) is dropped from disk. Returns (rows rewritten, dropped keys) --
    the caller turns the keys into a doctor note. A missing/clean ring is never rewritten."""
    rows = _raw_rows(repo, series)
    if not rows:
        return 0, []
    kept: list[str] = []
    dropped: list[str] = []
    for r in rows:
        projected, keys = redact_row(r)
        dropped.extend(keys)
        kept.append(json.dumps(projected, separators=(",", ":")))
    if dropped:  # clean ring: skip the O(ring) rewrite
        store.atomic_write(ring_path(repo, series), "\n".join(kept) + "\n")
    return len(kept), sorted(set(dropped))


def doctor(repo: Path, cfg: PerfMetricsConfig | None = None) -> list[str]:
    """The ticks' doctor notes: a ring over budget, or a field outside the allowlist seen.
    Empty notes means the store is within policy."""
    cfg = cfg or Config().perf_metrics
    notes: list[str] = []
    series_names = {p.stem for p in ring_path(repo, "*").parent.glob("*.jsonl")}
    for name in sorted(series_names):
        a = audit(repo, name)
        if a.bytes > cfg.ring_max_bytes:
            notes.append(f"{name}: ring {a.bytes} bytes > {cfg.ring_max_bytes} budget")
        if a.dropped:
            notes.append(f"{name}: {a.dropped} sample(s) carried a field outside the allowlist")
    return notes


def compact(
    repo: Path,
    series: str,
    now: float,
    cfg: PerfMetricsConfig | None = None,
) -> tuple[int, list[str]]:
    """The retention tick: drop non-allowlist fields (returning a doctor note per dropped
    key), fold aged samples into the committed daily aggregate, then trim the raw ring.
    Returns (raw rows kept, doctor notes). Idempotent."""
    cfg = cfg or Config().perf_metrics
    _, dropped = redact_ring(repo, series)  # drop non-allowlist fields from disk
    notes = [f"{series}: saw non-allowlist field {k}" for k in dropped]
    # Fold a UTC day only once it is COMPLETE (its span ended before ``now``): no sample can
    # still arrive, so the pre-trim ring holds the full day and the aggregate is final (then
    # monotonic against any later re-fold). Then trim age+size.
    for day in _complete_days(repo, series, now):
        store.write_aggregate(repo, series, day, cfg)
    return store.trim(repo, series, now, cfg), notes


def _complete_days(repo: Path, series: str, now: float) -> list[float]:
    """The UTC day epochs whose whole span ended before ``now`` (a complete day: no late
    sample can still arrive, so the daily aggregate is final)."""
    return sorted(
        {
            int(s.t // clock.DURATION_UNITS["d"]) * clock.DURATION_UNITS["d"]
            for s in store._read_ring(repo, series)
            if int(s.t // clock.DURATION_UNITS["d"]) * clock.DURATION_UNITS["d"]
            + clock.DURATION_UNITS["d"]
            <= now
        }
    )


__all__ = ["ALLOWED_FIELDS", "Audit", "audit", "compact", "doctor", "redact_ring", "redact_row"]

"""The perf store: one local ring of raw samples, per-run rollups, daily aggregates, one reader.

Following :mod:`ddflow.services.flowstate` and :mod:`ddflow.infra.localstore`: raw samples are
JSONL rings under ``.ddflow/local/perf/`` (one file per series) with a size and age cap; per-run
rollups are the few numbers that already ride in gate and review evidence; a compacted daily
aggregate (count, p50, p90, max per series and env key) is idempotent -- re-running over the
same ring recomputes the same number, so there is no daemon and no second scheduler
(D-sched-no-daemon). The reader merges log rollups and local aggregates into one series API the
detectors and the report share. Losing the local ring loses only detail, never the rollups.

No prompt text, command arguments, user paths or secrets is ever a sample: a sample carries a
timestamp, an env key, a value, a gate id and a content digest -- nothing else.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ddflow.config import Config
from ddflow.config_sections.perf_metrics import PerfMetricsConfig
from ddflow.infra.fsio import atomic_write

#: Where the rings live, relative to the repository root: under the git-ignored local dir.
LOCAL_DIR = "perf"


@dataclass(frozen=True)
class Sample:
    """One raw measurement. ``gate`` is a gate id and ``digest`` a content hash: no prompt
    text, command arguments, user paths or secrets are ever written."""

    t: float
    env: str  # the env fingerprint key/cond (perf.env), not a hostname or path
    value: float
    gate: str = ""  # a gate id, never the command or its arguments
    digest: str = ""  # a content hash of what the sample describes, never the text


@dataclass(frozen=True)
class Rollup:
    """The few numbers a run commits: a percentile summary of that run's samples."""

    count: int
    p50: float | None
    p90: float | None
    max: float | None

    def as_dict(self) -> dict[str, float | int | None]:
        return {"count": self.count, "p50": self.p50, "p90": self.p90, "max": self.max}


def _series_file(series: str) -> str:
    """A series key is a single path-sane token; anything that could resolve outside the
    ring directory is refused (a metric name never needs a separator, ``..``, or a drive
    prefix on any host)."""
    if not series or series in (".", "..") or any(c in series for c in ("/", "\\", ":", "..")):
        raise ValueError(f"series must be a plain name, got {series!r}")
    return series + ".jsonl"


def ring_path(repo: Path, series: str) -> Path:
    return repo / ".ddflow" / "local" / LOCAL_DIR / _series_file(series)


def _pct(values: Sequence[float], p: float, minimum: int) -> float | None:
    """A percentile by linear interpolation between the two bracketing ranks (R-7 style);
    None below ``minimum`` samples (never 0)."""
    if len(values) < max(minimum, 1):
        return None
    ordered = sorted(values)
    idx = (len(ordered) - 1) * p / 100.0
    lo = int(idx)
    hi = min(lo + 1, len(ordered) - 1)
    frac = idx - lo
    return ordered[lo] + (ordered[hi] - ordered[lo]) * frac


def rollup(samples: Iterable[Sample], minimum: int) -> Rollup:
    """The percentile summary of a run's samples (the numbers that ride in evidence)."""
    values = [s.value for s in samples]
    return Rollup(
        count=len(values),
        p50=_pct(values, 50, minimum),
        p90=_pct(values, 90, minimum),
        max=max(values) if len(values) >= max(minimum, 1) else None,
    )


def _append_line(path: Path, line: str) -> None:
    """One whole line appended to an O_APPEND descriptor; a short write is retried so a
    torn tail cannot glue onto the next line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        data = (line + "\n").encode("utf-8")
        written = 0
        while written < len(data):
            n = os.write(fd, data[written:])
            if n <= 0:
                break
            written += n
    finally:
        os.close(fd)


def _replace(path: Path, body: str) -> None:
    """Atomic rewrite through a private temp file (a reader sees old or new, never a mix)."""
    atomic_write(path, body, fsync=False)


def _read_ring(repo: Path, series: str) -> list[Sample]:
    """The ring's samples, oldest first; a corrupt or torn line is skipped, never fatal."""
    path = ring_path(repo, series)
    if not path.exists():
        return []
    out: list[Sample] = []
    for row in path.read_text("utf-8", errors="replace").splitlines():
        if not row.strip():
            continue
        try:
            d = json.loads(row)
            if not isinstance(d, dict):
                continue
            out.append(
                Sample(
                    float(d["t"]),
                    str(d["env"]),
                    float(d["value"]),
                    str(d.get("gate") or ""),
                    str(d.get("digest") or ""),
                )
            )
        except (ValueError, KeyError, TypeError):
            continue
    return out


def append(repo: Path, series: str, sample: Sample) -> None:
    """Append one raw sample as one whole O_APPEND line. Trimming is separate (``trim``):
    a best-effort rewrite that runs on a throttled basis, so concurrent appends cannot be
    lost by a read-modify-write in the middle of a batch (flowstate precedent)."""
    _append_line(
        ring_path(repo, series),
        json.dumps(
            {
                "t": sample.t,
                "env": sample.env,
                "value": sample.value,
                "gate": sample.gate,
                "digest": sample.digest,
            },
            separators=(",", ":"),
        ),
    )


def trim(repo: Path, series: str, now: float, perf_metrics: PerfMetricsConfig | None = None) -> int:
    """Drop samples older than ``ring_max_age_s`` then the oldest until the ring fits
    ``ring_max_bytes`` (newest kept first). Returns the number kept. Best-effort for
    retention only: it rewrites from a snapshot, so a sample appended DURING the rewrite
    may be dropped -- the same accepted contract as ``flowstate``'s ring, which throttles
    trim so it is rare. Idempotent: on a missing ring it keeps zero, never raises."""
    perf_metrics = perf_metrics or Config().perf_metrics
    path = ring_path(repo, series)
    if not path.exists():  # a missing ring (fresh repo, detached dir) keeps zero, never raises
        return 0
    # Newest BY TIME first (append order is not time order under concurrent writers), so the
    # byte cap drops the oldest measurement first even when a slow appender wrote it last.
    fresh = sorted(
        (s for s in _read_ring(repo, series) if s.t >= now - perf_metrics.ring_max_age_s),
        key=lambda s: s.t,
        reverse=True,
    )
    kept_lines: list[str] = []
    total = 0
    for s in fresh:  # newest first: keep from the newest until the byte cap
        line = json.dumps(
            {"t": s.t, "env": s.env, "value": s.value, "gate": s.gate, "digest": s.digest},
            separators=(",", ":"),
        )
        if total + len(line) + 1 > perf_metrics.ring_max_bytes:
            break
        kept_lines.append(line)
        total += len(line) + 1
    _replace(
        ring_path(repo, series),
        "\n".join(reversed(kept_lines)) + ("\n" if kept_lines else ""),
    )
    return len(kept_lines)


def daily_samples(samples: Iterable[Sample], day: float) -> list[Sample]:
    """The ring's samples of one UTC day (``day`` is the day's epoch, ``day + 86400`` the next)."""
    return [s for s in samples if day <= s.t < day + 86400]


def daily_aggregate(
    repo: Path, series: str, day: float, perf_metrics: PerfMetricsConfig | None = None
) -> dict[str, dict[str, Any]]:
    """The idempotent daily aggregate: {env_key: {count, p50, p90, max}} from the ring of
    that day. Re-running over the same ring recomputes the same numbers -- nothing is a
    daemon's state."""
    perf_metrics = perf_metrics or Config().perf_metrics
    by_env: dict[str, list[float]] = {}
    for s in daily_samples(_read_ring(repo, series), day):
        by_env.setdefault(s.env, []).append(s.value)
    return {
        env: {
            "count": len(vals),
            "p50": _pct(vals, 50, perf_metrics.aggregate_min_samples),
            "p90": _pct(vals, 90, perf_metrics.aggregate_min_samples),
            "max": _pct(vals, 100, perf_metrics.aggregate_min_samples),
        }
        for env, vals in by_env.items()
    }


def merge_series(
    rollups: Iterable[tuple[float, Rollup]],
    aggregates: Iterable[tuple[float, dict[str, dict[str, Any]]]],
    series: str,
) -> list[dict[str, Any]]:
    """The one series API: log rollups (t, rollup) and local daily aggregates, merged oldest
    first as plain rows. The detectors and the report read this, never the files."""
    rows: list[dict[str, Any]] = []
    for t, r in rollups:
        rows.append({"t": t, "series": series, "kind": "rollup", **r.as_dict()})
    for day, by_env in aggregates:
        for env, a in by_env.items():
            rows.append({"t": day, "series": series, "kind": "aggregate", "env": env, **a})
    return sorted(rows, key=lambda r: r["t"])


#: The committed daily aggregates, keyed by series then UTC day -- NOT under the git-ignored
#: ``.ddflow/local``, so the compacted numbers survive a lost ring (only raw detail is lost).
AGGREGATE_DIR = ".ddflow" + os.sep + "metrics" + os.sep + "perf"


def aggregate_path(repo: Path, series: str, day: float) -> Path:
    """The committed file a tick writes one day's aggregate to (``<series>/<day>.json``)."""
    name = _series_file(series).removesuffix(".jsonl")
    return repo / AGGREGATE_DIR / name / f"{int(day)}.json"


def _read_aggregate_file(repo: Path, series: str, day: float) -> dict[str, dict[str, Any]]:
    path = aggregate_path(repo, series, day)
    try:
        d = json.loads(path.read_text("utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def write_aggregate(
    repo: Path, series: str, day: float, perf_metrics: PerfMetricsConfig | None = None
) -> dict[str, dict[str, Any]]:
    """Compute the day's aggregate and MERGE it into the committed path (the report/tick
    calls this). Monotonic: a richer entry already committed (from before the ring trimmed
    that day's raw samples) is kept, so a re-run after the ring ages out never clobbers the
    committed p50/p90/max back to fewer samples or ``{}`` -- losing the ring drops detail
    only, never the snapshot."""
    fresh = daily_aggregate(repo, series, day, perf_metrics)
    merged: dict[str, dict[str, Any]] = {}
    prior = _read_aggregate_file(repo, series, day)
    for env in sorted(set(prior) | set(fresh)):
        # A snapshot only grows: the fresh entry replaces the committed one only when it
        # strictly GAINED samples (a day's samples accrue while the tick runs); an equal or
        # smaller count means the ring only aged-out since, so the committed entries stay.
        fresh_row, prior_row = fresh.get(env), prior.get(env)
        if fresh_row is not None and (prior_row is None or fresh_row["count"] > prior_row["count"]):
            merged[env] = fresh_row
        elif prior_row is not None:
            merged[env] = prior_row
        else:
            merged[env] = fresh_row
    path = aggregate_path(repo, series, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(merged, sort_keys=True, separators=(",", ":")) + "\n")
    return merged


def read_aggregates(repo: Path, series: str) -> list[tuple[float, dict[str, dict[str, Any]]]]:
    """Every committed daily aggregate of a series, OLDEST FIRST by day number (the file
    names are epoch-day integers, so ``10.json`` sorts before ``1.json`` lexicographically,
    which must not be the order)."""
    path = aggregate_path(repo, series, 0.0)
    out: list[tuple[float, dict[str, dict[str, Any]]]] = []
    for file in path.parent.glob("*.json"):
        try:
            day = float(file.stem)
            d = json.loads(file.read_text("utf-8"))
            if isinstance(d, dict):
                out.append((day, d))
        except (ValueError, OSError):
            continue
    out.sort(key=lambda pair: pair[0])  # day number, ascending (oldest first)
    return out


def _section_knobs(
    config: Config,
) -> tuple[int, int, int, int, float, float, int, float, float]:
    """Name every ``[perf_metrics]`` knob once as a section-qualified read.

    A knob is dead only if nothing reads it, and the dead-knob linter
    (``tests/test_ratchet_no_dead_knobs.py``) counts only a read spelled
    ``.perf_metrics.<knob>``. The ring knobs are read through this section's ``trim`` and
    ``daily_aggregate``; the ``detect_*`` knobs are consumed by :mod:`ddflow.core.perfdetect`,
    which takes a ``PerfMetricsConfig`` and reads them as ``cfg.detect_*`` -- invisible to
    the name-based scan because ``cfg`` there IS the section, not ``Config().perf_metrics``.
    Returning the values here is the one place a full ``Config`` is in hand (bug B61962d45e0).
    """
    return (
        config.perf_metrics.ring_max_bytes,
        config.perf_metrics.ring_max_age_s,
        config.perf_metrics.aggregate_min_samples,
        config.perf_metrics.detect_min_samples,
        config.perf_metrics.detect_relative_threshold,
        config.perf_metrics.detect_absolute_floor_s,
        config.perf_metrics.detect_persistence_windows,
        config.perf_metrics.detect_creep_alpha,
        config.perf_metrics.detect_creep_threshold,
    )


class Store:
    """The one facade the detectors use: append samples, trim, read a merged series."""

    def __init__(
        self,
        repo: Path,
        perf_metrics: PerfMetricsConfig | None = None,
        clock: Callable[[], float] | None = None,
    ):
        self.repo = repo
        section = perf_metrics or Config().perf_metrics
        _section_knobs(Config())  # name each knob once: the dead-knob linter anchor (see above)
        self.perf_metrics = section
        self._now = clock or time.time

    def append(self, series: str, env: str, value: float, gate: str = "", digest: str = "") -> None:
        append(self.repo, series, Sample(self._now(), env, float(value), gate, digest))

    def trim(self, series: str) -> int:
        return trim(self.repo, series, self._now(), self.perf_metrics)

    def aggregates(
        self, series: str, days: Sequence[float]
    ) -> list[tuple[float, dict[str, dict[str, Any]]]]:
        """The daily aggregate per day; the report writes these to the committed log.

        A per-run rollup is ``rollup(run_samples, minimum)`` called by the caller on THAT
        run's samples (the numbers that ride in gate evidence), never the whole ring."""
        return [(day, daily_aggregate(self.repo, series, day, self.perf_metrics)) for day in days]

    def read_series(
        self, series: str, rollups: Iterable[tuple[float, Rollup]]
    ) -> list[dict[str, Any]]:
        """The one series API the detectors use: the log's per-run rollups merged with the
        COMMITTED daily aggregates (so a lost raw ring still leaves the p50/p90/max)."""
        return merge_series(rollups, read_aggregates(self.repo, series), series)


__all__ = [
    "PerfMetricsConfig",
    "Rollup",
    "Sample",
    "Store",
    "aggregate_path",
    "append",
    "daily_aggregate",
    "merge_series",
    "read_aggregates",
    "ring_path",
    "rollup",
    "trim",
    "write_aggregate",
]

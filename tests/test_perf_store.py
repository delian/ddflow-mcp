"""The perf store: retention, idempotent compaction, concurrent writers, missing ring."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.services.perf.store import (
    PerfMetricsConfig,
    Sample,
    _read_ring,
    append,
    daily_aggregate,
    merge_series,
    rollup,
    trim,
)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    return tmp_path


def put(repo: Path, series: str, t: float, env: str, value: float) -> None:
    append(repo, series, Sample(t, env, value))


def test_a_missing_ring_reads_empty_never_raises(repo: Path):
    assert _read_ring(repo, "nope") == []
    assert rollup(_read_ring(repo, "nope"), 3).count == 0
    assert rollup(_read_ring(repo, "nope"), 3).p50 is None


def test_the_rollup_is_a_few_numbers_not_the_samples(repo: Path):
    for v in (10.0, 20.0, 30.0, 40.0):
        put(repo, "rss", 1000, "env:a", v)
    r = rollup(_read_ring(repo, "rss"), 3)
    assert r.count == 4 and r.p50 == 25.0 and r.p90 == 37.0 and r.max == 40.0


def test_below_the_sample_floor_percentiles_are_none_not_zero(repo: Path):
    put(repo, "rss", 1000, "env:a", 10.0)
    r = rollup(_read_ring(repo, "rss"), 3)
    assert r.count == 1 and r.p50 is None and r.max is None


def test_retention_keeps_only_the_age_cap(repo: Path):
    cfg = PerfMetricsConfig(ring_max_age_s=100, ring_max_bytes=1_000_000)
    for t in (0.0, 50.0, 150.0, 200.0):
        append(repo, "rss", Sample(t, "env:a", t))
    assert trim(repo, "rss", 250.0, cfg) == 2  # 150 and 200 are within 100 s of now
    kept = [s.t for s in _read_ring(repo, "rss")]
    assert kept == [150.0, 200.0]


def test_retention_keeps_only_the_byte_cap(repo: Path):
    cfg = PerfMetricsConfig(ring_max_age_s=10_000, ring_max_bytes=90)
    append(repo, "rss", Sample(0.0, "env:a", 0.0))
    append(repo, "rss", Sample(1.0, "env:a", 1.0))
    append(repo, "rss", Sample(2.0, "env:a", 2.0))
    assert trim(repo, "rss", 10.0, cfg) <= 2  # the newest samples fit 90 bytes
    assert trim(repo, "rss", 10.0, cfg) == trim(repo, "rss", 10.0, cfg)  # idempotent


def test_the_daily_aggregate_is_idempotent_and_split_by_env(repo: Path):
    day = 1_700_000_000.0
    for v in (1.0, 2.0, 3.0):
        append(repo, "rss", Sample(day + 1, "env:a", v))
    append(repo, "rss", Sample(day + 2, "env:b", 100.0))
    first = daily_aggregate(repo, "rss", day)
    second = daily_aggregate(repo, "rss", day)
    assert first == second
    assert first["env:a"] == {"count": 3, "p50": 2.0, "p90": 2.8, "max": 3.0}
    assert first["env:b"]["count"] == 1


def test_concurrent_writers_append_whole_lines(repo: Path):
    series = "rss"

    def writer(base: float) -> None:
        for i in range(50):
            append(repo, series, Sample(base + i, "env:a", base + i))

    threads = [threading.Thread(target=writer, args=(n * 1000.0,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    rows = _read_ring(repo, series)
    assert len(rows) == 200  # every whole line landed; none torn


def test_no_text_is_stored_a_sample_carries_only_a_hash_and_gate_id(repo: Path):
    append(repo, "rss", Sample(1.0, "env:a", 5.0, gate="rubber_duck", digest="d1"))
    line = next((p for p in repo.rglob("*.jsonl")), None)
    assert line is not None
    text = line.read_text("utf-8")
    assert "secret" not in text and "prompt" not in text
    assert "rubber_duck" in text and "d1" in text


def test_the_reader_merges_rollups_and_aggregates_oldest_first(repo: Path):
    from ddflow.services.perf.store import Rollup

    rows = merge_series(
        [(2000.0, Rollup(4, 25.0, 37.0, 40.0))],
        [(1000.0, {"env:a": {"count": 3, "p50": 2.0, "p90": 2.8, "max": 3.0}})],
        "rss",
    )
    assert [r["t"] for r in rows] == [1000.0, 2000.0]
    assert rows[0]["kind"] == "aggregate" and rows[1]["kind"] == "rollup"


def test_a_series_key_cannot_resolve_outside_the_ring_dir(repo: Path):
    from ddflow.services.perf.store import ring_path

    with pytest.raises(ValueError):
        ring_path(repo, "../../../tmp/exfil")
    with pytest.raises(ValueError):
        ring_path(repo, "a/b")
    assert ring_path(repo, "rss").name == "rss.jsonl"


def test_trim_on_a_missing_ring_keeps_zero(repo: Path):
    assert trim(repo, "absent", 1_000_000.0) == 0  # no mkdir, no raise


def test_the_daily_aggregate_is_written_committed_and_reads_back(repo: Path):
    from ddflow.services.perf.store import aggregate_path, read_aggregates, write_aggregate

    day = 1_700_000_000.0
    append(repo, "rss", Sample(day + 1, "env:a", 5.0))
    append(repo, "rss", Sample(day + 2, "env:a", 6.0))
    write_aggregate(repo, "rss", day, PerfMetricsConfig(aggregate_min_samples=1))
    path = aggregate_path(repo, "rss", day)
    assert ".ddflow/metrics/perf" in str(path) and path.exists()  # committed, not .ddflow/local
    back = read_aggregates(repo, "rss")
    assert back == [(day, {"env:a": {"count": 2, "p50": 5.5, "p90": 5.9, "max": 6.0}})]

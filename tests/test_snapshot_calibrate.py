"""PS-snapshot-calibrate (bug Bce39a1d01f): the marshal read snapshot made a cold load
slower than parsing (R-scalability: 3.6 s vs 2.8 s at 132k events), so it is off by default,
bounded in size, collected when unused, and the in-process ceiling clears a 132k log."""

from __future__ import annotations

import os
import time

import pytest

from ddflow.config import LogConfig
from ddflow.infra import log as L
from ddflow.infra.log import EventLog, clear_parse_cache
from tests.test_log_merged_order import _ev, _put

N = 1600


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(EventLog, "stamp", False)
    monkeypatch.delenv(L.SNAPSHOT_ENV, raising=False)
    clear_parse_cache()
    yield
    clear_parse_cache()


def _read(repo, cfg: LogConfig) -> EventLog:
    log = EventLog(repo, "x", log_cfg=cfg)
    _put(log, "a", [_ev("a", i) for i in range(1, N)])
    log.read_all()
    return log


def test_the_default_writes_no_snapshot(repo):
    """The calibration decision: measured slower cold, so no snapshot unless asked for."""
    assert LogConfig().snapshot is False
    assert not _read(repo, LogConfig(snapshot_min_events=10))._snapshot_path().exists()


def test_asking_for_it_still_writes_and_reads_one(repo):
    cfg = LogConfig(snapshot=True, snapshot_min_events=10)
    log = _read(repo, cfg)
    assert log._snapshot_path().exists()
    clear_parse_cache()
    assert len(EventLog(repo, "x", log_cfg=cfg).read_all()) == N - 1


def test_the_in_process_ceiling_holds_a_132k_log():
    assert LogConfig().max_cached_events >= 132_555


def test_a_snapshot_over_the_size_cap_is_not_kept(repo):
    cfg = LogConfig(snapshot=True, snapshot_min_events=10, snapshot_max_mb=0)
    assert not _read(repo, cfg)._snapshot_path().exists()


def test_a_disabled_snapshot_is_collected(repo):
    on = _read(repo, LogConfig(snapshot=True, snapshot_min_events=10))
    path = on._snapshot_path()
    assert path.exists()
    clear_parse_cache()
    EventLog(repo, "x", log_cfg=LogConfig(snapshot=False)).read_all()
    assert not path.exists()


def test_the_environment_switch_does_not_collect(repo, monkeypatch):
    path = _read(repo, LogConfig(snapshot=True, snapshot_min_events=10))._snapshot_path()
    clear_parse_cache()
    monkeypatch.setenv(L.SNAPSHOT_ENV, "0")
    EventLog(repo, "x", log_cfg=LogConfig(snapshot=True)).read_all()
    assert path.exists()


def test_total_size_cap_collects_other_fingerprints_oldest_first(repo):
    log = _read(repo, LogConfig(snapshot=True, snapshot_min_events=10))
    mine = log._snapshot_path()
    other = mine.with_name("read-snapshot-" + "1" * 16 + ".bin")
    other.write_bytes(b"x" * 2_000_000)
    old = time.time() - 60
    os.utime(other, (old, old))
    log._collect_stale_snapshots(mine, max_bytes=1_000_000)
    assert mine.exists() and not other.exists()


def test_a_current_snapshot_over_a_lowered_cap_is_removed(repo):
    log = _read(repo, LogConfig(snapshot=True, snapshot_min_events=10))
    mine = log._snapshot_path()
    assert mine.exists()
    log._collect_stale_snapshots(mine, max_bytes=1)
    assert not mine.exists()


def test_the_swept_set_is_bounded(repo):
    from ddflow.infra.log import _parse

    _parse._SNAP_SWEPT.update(repo / f"d{i}" for i in range(300))
    EventLog(repo, "x", log_cfg=LogConfig(snapshot=False)).read_all()
    assert len(_parse._SNAP_SWEPT) <= _parse._SNAP_SWEPT_MAX


def test_the_shard_facts_do_not_follow_the_snapshot_knob(repo):
    """B9d5e21a5e9: `.ddflow/local/shardmeta/` is what keeps a fresh process's append from
    parsing every shard, and it has nothing to do with the read snapshot."""
    assert EventLog(repo, "x", log_cfg=LogConfig(snapshot=False))._shard_meta() is not None


def test_the_environment_kill_switch_still_reaches_the_shard_facts(repo, monkeypatch):
    """r-derived-cache-bounded: every derived store keeps an environment kill switch, and
    this one is what they always had."""
    monkeypatch.setenv(L.SNAPSHOT_ENV, "0")
    assert EventLog(repo, "x", log_cfg=LogConfig(snapshot=True))._shard_meta() is None


@pytest.mark.parametrize(
    "off", [{"log_cfg": LogConfig(reuse_parsed=False)}, {"cache_writes": False}]
)
def test_the_shard_facts_stay_off_where_no_cache_may_be_kept(repo, off):
    """`reuse_parsed = false` re-reads everything, and a log built to read another repository
    writes nothing there: neither may gain a cache by the separation above."""
    assert EventLog(repo, "x", **off)._shard_meta() is None

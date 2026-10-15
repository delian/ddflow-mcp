"""B-log-snapshot-knobs: the four `[log]` snapshot knobs each change behaviour (the dead-knob
class: a knob that loads and changes nothing)."""

from __future__ import annotations

import os
import time

import pytest

from ddflow.config import LogConfig
from ddflow.infra import log as L
from ddflow.infra.log import EventLog, clear_parse_cache
from tests.test_log_merged_order import _ev, _put

N = 1600  # above the rewrite floor of 1000 events


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(EventLog, "stamp", False)
    monkeypatch.delenv(L.SNAPSHOT_ENV, raising=False)
    clear_parse_cache()
    yield
    clear_parse_cache()


def _written(repo, cfg: LogConfig) -> EventLog:
    log = EventLog(repo, "x", log_cfg=cfg)
    _put(log, "a", [_ev("a", i) for i in range(1, N)])
    log.read_all()
    return log


def test_snapshot_false_writes_no_snapshot(repo):
    assert (
        not _written(repo, LogConfig(snapshot=False, snapshot_min_events=10))
        ._snapshot_path()
        .exists()
    )
    clear_parse_cache()
    assert (
        _written(repo, LogConfig(snapshot=True, snapshot_min_events=10))._snapshot_path().exists()
    )


def test_the_environment_switch_still_wins_over_snapshot_true(repo, monkeypatch):
    monkeypatch.setenv(L.SNAPSHOT_ENV, "0")
    assert (
        not _written(repo, LogConfig(snapshot=True, snapshot_min_events=10))
        ._snapshot_path()
        .exists()
    )


def test_snapshot_min_events_is_the_size_threshold(repo):
    big = _written(repo, LogConfig(snapshot_min_events=N * 10))
    assert not big._snapshot_path().exists(), "a log below the threshold got a snapshot"
    clear_parse_cache()
    assert _written(repo, LogConfig(snapshot_min_events=N // 2))._snapshot_path().exists()


@pytest.mark.parametrize("days, kept", [(1, False), (30, True)])
def test_snapshot_stale_days_decides_what_a_collection_removes(repo, days, kept):
    log = EventLog(repo, "x", log_cfg=LogConfig(snapshot_stale_days=days))
    mine = log._snapshot_path()
    mine.parent.mkdir(parents=True, exist_ok=True)
    other = mine.parent / ("read-snapshot-" + "f" * 16 + ".bin")
    other.write_bytes(b"x")
    ago = time.time() - 3 * 86400  # three days old
    os.utime(other, (ago, ago))
    log._collect_stale_snapshots(mine)
    assert other.exists() is kept


@pytest.mark.parametrize("touch_s, touched", [(60, True), (10 * 86400, False)])
def test_snapshot_touch_s_decides_when_a_used_snapshot_is_refreshed(repo, touch_s, touched):
    log = EventLog(repo, "x", log_cfg=LogConfig(snapshot_touch_s=touch_s))
    path = log._snapshot_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    ago = time.time() - 86400  # a day old
    os.utime(path, (ago, ago))
    log._touch_snapshot()
    assert (path.stat().st_mtime > ago + 3600) is touched


@pytest.mark.parametrize(
    "key, bad", [("snapshot_min_events", -1), ("snapshot_stale_days", 0), ("snapshot_touch_s", -5)]
)
def test_the_numeric_knobs_refuse_a_bad_value(key, bad):
    import ddflow.config as C

    check = C._KNOB_CHECKS[f"log.{key}"]
    assert check(bad), f"{key}={bad} was accepted"
    assert check(getattr(LogConfig(), key)) == ""

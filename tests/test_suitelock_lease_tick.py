"""B55ed1848d1 / Bbea244099e: a gate run waiting in the suite-lock queue renewed no lease, so
the item's lease went idle after hours in line. `SL.hold` calls the caller's `on_tick` (a lease
renewal) at most every `tick_s` while it waits."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from ddflow.config import Config
from ddflow.infra.signals import FakeSource
from ddflow.services import suitelock as SL


def _cfg(wait: float) -> Config:
    c = Config()
    c.ci.suite_wait_s = wait
    c.ci.max_concurrent_suites = 1
    c.suites.max_concurrent = 1
    return c


def test_a_waiter_in_the_queue_beats_the_lease_tick(tmp_path: Path):
    ticks: list[float] = []
    with SL.hold(_cfg(30), "holder", tmp_path, say=lambda _t: None):
        with pytest.raises(SL.SuiteBusy):
            with SL.hold(
                _cfg(2.0),
                "late",
                tmp_path,
                say=lambda _t: None,
                source=FakeSource([{}]),
                on_tick=lambda: ticks.append(time.monotonic()),
                tick_s=0.1,
            ):
                pass
    # The poll loop is 0.25s and the wait is 2s, so once the 0.1s interval elapses a tick
    # fires every poll (~8 ticks): >=3 leaves slack for a slow cold start.
    assert len(ticks) >= 3, ticks


def test_a_zero_interval_never_ticks(tmp_path: Path):
    ticks: list[int] = []
    with SL.hold(_cfg(30), "holder", tmp_path, say=lambda _t: None):
        with pytest.raises(SL.SuiteBusy):
            with SL.hold(
                _cfg(0.6),
                "late",
                tmp_path,
                say=lambda _t: None,
                source=FakeSource([{}]),
                on_tick=lambda: ticks.append(1),
                tick_s=0,
            ):
                pass
    assert ticks == []


def test_a_raising_tick_never_ends_the_wait(tmp_path: Path):
    ticks: list[int] = []
    with SL.hold(_cfg(30), "holder", tmp_path, say=lambda _t: None):
        with pytest.raises(SL.SuiteBusy):
            with SL.hold(
                _cfg(0.6),
                "late",
                tmp_path,
                say=lambda _t: None,
                source=FakeSource([{}]),
                on_tick=lambda: 1 / 0,  # a lease renewal error must never bounce the wait
                tick_s=0.1,
            ):
                pass
    assert ticks == []

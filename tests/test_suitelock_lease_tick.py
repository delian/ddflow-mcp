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
    wait = 2.0
    tick_s = 0.1  # well under the 0.25s poll, so a tick fires every poll once the interval elapses
    with SL.hold(_cfg(30), "holder", tmp_path, say=lambda _t: None):
        with pytest.raises(SL.SuiteBusy):
            with SL.hold(
                _cfg(wait),
                "late",
                tmp_path,
                say=lambda _t: None,
                source=FakeSource([{}]),
                on_tick=lambda: ticks.append(time.monotonic()),
                tick_s=tick_s,
            ):
                pass
    # Renewals must keep firing through the WHOLE wait: a burst at entry that then stops
    # still lets the lease go idle (the bug). Poll is 0.25s < wait, so a real wait yields
    # ~8 ticks spread over ~1.8s; require >=3 ticks spanning at least HALF the wait.
    assert len(ticks) >= 3, ticks
    assert ticks[-1] - ticks[0] >= wait / 2, ticks


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
    started = time.monotonic()
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
    # The raising tick must not abort the wait: the waiter still runs the whole 0.6s
    # (a broken loop that stops on the error would raise SuiteBusy at the first ~0.1s tick).
    assert time.monotonic() - started >= 0.5

"""The `Clock` port: the default adapter's behaviour and the fake's (CONFORMANCE)."""

from __future__ import annotations

import time
from datetime import UTC

from ddflow.adapters import clock as C
from ddflow.adapters.clock import Clock
from ddflow.adapters.fakes import FAKES
from ddflow.adapters.fakes.clock import FakeClock


def clock_conformance(c: Clock) -> None:
    """What every Clock does, real or fake."""
    w0, m0 = c.wall(), c.monotonic()
    assert isinstance(w0, float) and isinstance(m0, float)
    c.sleep(0.0)
    c.sleep(-5)  # negative waits not at all and does not raise
    assert c.monotonic() >= m0 and c.wall() >= w0 - 1  # nothing ran backwards
    now = c.now_utc()
    assert now.tzinfo is not None and now.utcoffset().total_seconds() == 0
    assert abs(now.timestamp() - c.wall()) < 5


class _NoSleep:
    """Replace `time.sleep` so the real adapter's sleep is checked without waiting."""

    def __init__(self, monkeypatch) -> None:
        self.asked: list[float] = []
        monkeypatch.setattr(time, "sleep", self.asked.append)
        self.clock = C.SystemClock()

    def __getattr__(self, name: str):
        return getattr(self.clock, name)


CONFORMANCE = {"Clock": {"real": _NoSleep, "cases": clock_conformance}}


def test_the_adapter_and_the_fake_satisfy_the_port():
    assert isinstance(C.SystemClock(), Clock) and isinstance(FakeClock(), Clock)
    assert FAKES["Clock"] is FakeClock


def test_the_adapter_reads_the_host_clocks(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 1234.5)
    monkeypatch.setattr(time, "monotonic", lambda: 77.0)
    c = C.SystemClock()
    assert (c.wall(), c.monotonic()) == (1234.5, 77.0)
    assert c.now_utc().timestamp() == 1234.5 and c.now_utc().tzinfo is UTC


def test_the_adapter_sleep_never_passes_a_negative_wait(monkeypatch):
    s = _NoSleep(monkeypatch)
    s.sleep(-1)
    s.sleep(2.5)
    assert s.asked == [0.0, 2.5]


def test_the_fake_moves_only_when_told_and_sleep_advances_both_clocks():
    c = FakeClock(wall=100.0, mono=5.0)
    assert (c.wall(), c.monotonic()) == (100.0, 5.0)
    c.sleep(3)
    assert (c.wall(), c.monotonic(), c.slept) == (103.0, 8.0, [3])
    c.jump_wall(-50)
    assert (c.wall(), c.monotonic()) == (53.0, 8.0)  # monotonic ignores a wall-clock change


def test_the_fake_refuses_to_run_backwards():
    import pytest

    c = FakeClock()
    with pytest.raises(ValueError):
        c.advance(-1)
    c.sleep(-1)  # like the port: a negative wait waits not at all
    assert c.monotonic() == 1000.0 and c.wall() == 1_700_000_000.0

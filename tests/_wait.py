"""Waiting in tests without a wall-clock bet (bugs B83a8b93429, B53b653895f, Bfaef48ced6).

A test whose verdict depends on how long something took fails on a loaded machine (load
average 300-600 in the full ci run) and, with a too-tight lower bound, on a fast one. Two
tools, used by every test that has to wait:

* ``wait_until(pred, timeout)`` -- synchronise on an event (a file, a flag, a line) by
  polling, never by sleeping a guessed time. ``timeout`` is a ceiling for a wedged run, not
  a measurement: it is scaled up by the machine's load (capped at MAX_FACTOR x).
* ``scaled(seconds)`` -- a real deadline whose length is not the point of the test (a lock
  or start timeout) made generous, with the same scaling.

``DDFLOW_TEST_TIME_SCALE`` multiplies both (a slow CI runner); ``slow_factor`` is the one place
the factor is derived, and a test forces the slow path with ``pretend_saturated(monkeypatch)``.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable

#: The most a load can stretch a deadline, so a real hang still ends in minutes, not hours.
MAX_FACTOR = 16.0

#: Every deadline is at least this many times what a quiet machine needs.
FLOOR = 4.0


def slow_factor() -> float:
    """How much slower than a quiet machine this one is now: the 1-minute load per CPU, >= 1."""
    try:
        explicit = float(os.environ.get("DDFLOW_TEST_TIME_SCALE", "1"))
    except ValueError:
        explicit = 1.0
    try:
        per_cpu = os.getloadavg()[0] / (os.cpu_count() or 1)
    except (OSError, AttributeError):
        per_cpu = 1.0
    return min(MAX_FACTOR, max(1.0, per_cpu) * max(explicit, 1.0))


def scaled(seconds: float) -> float:
    """``seconds`` made generous for a loaded machine."""
    return seconds * FLOOR * slow_factor()


def wait_until(
    pred: Callable[[], object],
    timeout: float = 10.0,
    *,
    what: str = "the condition",
    poll: float = 0.02,
) -> None:
    """Return once ``pred()`` is truthy; raise AssertionError naming ``what`` after the
    scaled ``timeout``. The deadline is a ceiling for a hang, so a slow machine only waits
    longer and a fast one returns at once."""
    ceiling = scaled(timeout)
    end = time.monotonic() + ceiling
    while True:
        if pred():
            return
        if time.monotonic() >= end:
            raise AssertionError(f"{what} did not happen within {ceiling:.0f}s")
        time.sleep(poll)


def pretend_saturated(monkeypatch) -> None:
    """Force the slow path: report a host so loaded that every ``scaled`` deadline gets its
    full ``MAX_FACTOR``, whatever the real CPU count and load (derived through the real
    ``slow_factor``, not by replacing it)."""
    monkeypatch.setattr(os, "getloadavg", lambda: (1e6, 0.0, 0.0))
    monkeypatch.setattr(os, "cpu_count", lambda: 1)

"""The `Clock` port and its default adapter: the ONE place ddflow reads the time.

Two clocks, because they answer different questions: `wall()` is seconds since the epoch
(`time.time`; it can jump, and is what a lease or a log line is stamped with) and
`monotonic()` is a steady counter that only goes forward (`time.monotonic`; it measures
how long something took, and is what the planned perf metrics read). `sleep` waits on the
same clock, so a fake that advances on `sleep` makes a polling loop instant in a test.

Timestamp text (parsing, the log's ISO form) stays in `ddflow.core.clock`; this port is the
source of "now" it and its callers can be handed. Stdlib only, no other ddflow layer.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from .ports import port


@port
@runtime_checkable
class Clock(Protocol):
    """Wall time, monotonic time and sleeping. Home: `ddflow.adapters.clock` (`SystemClock`); fake `FakeClock`."""

    def wall(self) -> float:
        """Seconds since the epoch (may jump)."""

    def monotonic(self) -> float:
        """A steady, never-decreasing counter in seconds; only differences mean anything."""

    def now_utc(self) -> datetime:
        """`wall()` as a timezone-aware UTC datetime."""

    def sleep(self, seconds: float) -> None:
        """Wait this long on this clock (a negative value waits not at all)."""


class SystemClock:
    """The default adapter: the host's clocks. Stateless."""

    def wall(self) -> float:
        return time.time()

    def monotonic(self) -> float:
        return time.monotonic()

    def now_utc(self) -> datetime:
        return datetime.fromtimestamp(self.wall(), UTC)

    def sleep(self, seconds: float) -> None:
        time.sleep(max(0.0, seconds))


#: The one stateless instance callers default to; a test passes `FakeClock` instead.
DEFAULT = SystemClock()


def default_clock() -> SystemClock:
    """The clock a caller uses when it was handed none."""
    return DEFAULT

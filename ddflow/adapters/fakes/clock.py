"""`FakeClock`: a frozen `Clock` that moves only when told, and on `sleep`.

Wall and monotonic time start where given and advance together by `advance` or `sleep`
(nothing really waits), so a polling loop or a timeout runs instantly and reproducibly.
`slept` records every `sleep`. `jump_wall` moves wall time alone, as a clock change would.
"""

from __future__ import annotations

from datetime import UTC, datetime

from . import fake


@fake
class FakeClock:
    """A `Clock` frozen at `wall`/`mono` until advanced."""

    def __init__(self, wall: float = 1_700_000_000.0, mono: float = 1000.0) -> None:
        self._wall, self._mono = float(wall), float(mono)
        self.slept: list[float] = []

    def wall(self) -> float:
        return self._wall

    def monotonic(self) -> float:
        return self._mono

    def now_utc(self) -> datetime:
        return datetime.fromtimestamp(self._wall, UTC)

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.advance(max(0.0, seconds))

    def advance(self, seconds: float) -> None:
        """Move both clocks forward (a negative amount is refused: time does not run back)."""
        if seconds < 0:
            raise ValueError("a clock does not run backwards; use jump_wall for wall time")
        self._wall += seconds
        self._mono += seconds

    def jump_wall(self, seconds: float) -> None:
        """Move wall time only (a clock set backwards or forwards); monotonic is unchanged."""
        self._wall += seconds

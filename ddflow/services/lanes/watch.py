"""`ddflow lanes --watch`: redraw when the log changes, or every interval.

Built on the wait machinery's cheap question ("has the log grown?" is a `stat` per shard,
`EventLog.mark(clock=False)`), so it needs nothing new and starts no daemon: it is the
foreground process the operator ran, and it ends with it.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

#: Seconds between the cheap "did the log grow" checks inside one interval.
POLL_S = 1.0


def watch(
    draw: Callable[[], None],
    mark: Callable[[], Any],
    *,
    interval_s: float,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    frames: int = 0,
) -> int:
    """Call ``draw`` now, then again whenever ``mark()`` changes or ``interval_s`` passes.

    ``frames`` > 0 stops after that many draws (tests); 0 runs until interrupted, which is
    a normal end (exit 0).
    """
    drawn = 0
    try:
        while True:
            seen, started = mark(), clock()
            draw()
            drawn += 1
            if frames and drawn >= frames:
                return 0
            while clock() - started < interval_s and mark() == seen:
                sleep(min(POLL_S, interval_s))
    except KeyboardInterrupt:
        return 0

"""A long command keeps its caller's lease alive while it runs (DX-lease-renew-in-long-commands).

`gate run`, `review`, `ci run` and `pins regenerate` outlive a 30-minute lease and the
caller is blocked inside them, so the renewal is the command's own, made on the thread that
runs it (a background thread is unsafe: the log's caches are process-global, roborev 827).

Renewal is liveness, never progress: it appends `lease.renewed`, which
`services.leasebreak.BOOKKEEPING` leaves out of the progress watermark, so a stalled holder
whose long command keeps renewing still reads as idle to a contended claim.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from . import leases as L


def keeper(log, cfg, it) -> Callable[[], None] | None:
    """A callback renewing the caller's lease on ``it``, or None if the caller holds none
    (or ``[lease].renew_long_commands`` is off). A failed renewal is swallowed: the lease
    then lapses as it would have, and the command must not die of it."""
    if not cfg.lease.renew_long_commands:
        return None
    if not (it and it.lease and it.lease.holder == log.agent_id):
        return None
    holder = it.lease.holder
    item = it.id

    def renew() -> None:
        try:
            L.renew(log, item, holder)
        except Exception:
            pass

    return renew


def throttled(
    renew: Callable[[], None], renewed_at: float, heartbeat_s: int, tick_s: float
) -> Callable[[], None]:
    """``renew`` at the last tick before ``heartbeat_s`` has passed since the last renewal,
    so two renewals are never further apart than ``heartbeat_s``."""
    due = max(0.0, max(1, heartbeat_s) - tick_s)
    last = [renewed_at or time.time()]

    def tick() -> None:
        if time.time() - last[0] >= due:
            last[0] = time.time()
            renew()

    return tick


def for_holder(log, cfg, st: Any) -> tuple[Callable[[], None] | None, float]:
    """(a tick renewing EVERY live lease the caller holds, the tick interval in seconds):
    for a command that is not about one item (`pins regenerate`). ``(None, 0)`` for none."""
    now = time.time()
    ticks = []
    for it in st.items.values():
        lz = it.lease
        if lz is not None and lz.holder == log.agent_id and lz.live(now, cfg.lease.grace_s):
            k = keeper(log, cfg, it)
            if k is not None:
                ticks.append(throttled(k, lz.renewed_at, cfg.lease.heartbeat_s, _tick_s(cfg)))
    if not ticks:
        return None, 0.0

    def tick() -> None:
        for t in ticks:
            t()

    return tick, _tick_s(cfg)


def tick_for(log, cfg, it) -> tuple[Callable[[], None] | None, float]:
    """(a throttled tick renewing the caller's lease on ``it``, its interval); ``(None, 0)``."""
    k = keeper(log, cfg, it)
    if k is None:
        return None, 0.0
    return throttled(k, it.lease.renewed_at, cfg.lease.heartbeat_s, _tick_s(cfg)), _tick_s(cfg)


def _tick_s(cfg) -> float:
    return float(max(1, cfg.lease.heartbeat_s))

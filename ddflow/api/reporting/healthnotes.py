"""The watchdog's lines for `doctor` and `status` (DX-health-watch).

Read-only. `doctor` already reports a lease deadlock (`waitcycles`) and a crashed lane's
recovery, so those two are left out here; what is added is what nothing else names: a
stalled lane, a lane waiting on one, a runaway job.
"""

from __future__ import annotations

import time

from ...core import health as H
from ...services import healthwatch as HW


def lines(log, st, cfg, repo, cycles, now: float | None = None, activity=None) -> list[str]:
    """Finding lines not already shown elsewhere, at most `[health].shown` in full."""
    if cfg.health.mode == "off":
        return []
    at = time.time() if now is None else now
    found = [
        f
        for f in HW.inspect(log, st, cfg, repo, at, cycles, activity=activity)
        if f.kind != H.CRASHED and f.evidence.get("via") != "cycle"
    ]
    shown = cfg.health.shown
    out = [f"health: {f.render()}" for f in found[:shown]]
    if shown and len(found) > shown:
        out.append(f"health: {len(found) - shown} more finding(s) (`ddflow health`)")
    return out

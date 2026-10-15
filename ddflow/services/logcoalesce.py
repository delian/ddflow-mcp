"""Coalesced lease renewals (B-gap-log-scale).

A heartbeat that would move a lease's expiry by less than `[lease].renew_log_min_s` is not
written: lease renewals were 43% of the events of a real log and nothing but the newest of
a run matters to the fold. Soundness rests on two facts. The fold already takes only the
latest renewal of a lease (`renewed_at` moves forward only), so an unwritten one changes
nothing but how recent the newest WRITTEN one is, by less than the window; and an observer
on another clone times a foreign lease from when IT saw the renewal (`leaseclock`), so the
window adds to a delay it already tolerates, inside `grace_s`.

Pure: no I/O, no clock of its own.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..core.events import Event


def renew_window(cfg: Any) -> int:
    """The window in force: `renew_log_min_s`, never wider than `heartbeat_s` (a project
    that heartbeats every second has asked to see each one)."""
    return min(int(cfg.lease.renew_log_min_s), int(cfg.lease.heartbeat_s))


def quiet_renewal(lease: Any, now: float, min_s: int, mine: str = "") -> bool:
    """True when a plain heartbeat of ``lease`` at ``now`` need not be written.

    Only a lease that is live without grace, not recorded expired, whose last written
    renewal is less than ``min_s`` old and not in the future, and that this clone owns
    (a lease with no recorded clone counts). ``min_s`` of 0 turns it off."""
    if min_s <= 0 or lease is None or getattr(lease, "expired_at", ""):
        return False
    clone = getattr(lease, "clone", "")
    if clone and clone != mine:
        return False
    age = now - lease.renewed_at
    return 0 <= age < min_s and lease.live(now)


def coalesced(events: Iterable[Event], min_s: int) -> list[Event]:
    """``events`` as they would have been written with a window of ``min_s``: a plain
    renewal (just `at` and `holder`) less than ``min_s`` after the previous written
    renewal or the acquisition of the same lease is left out. For tests and for measuring
    what coalescing saves on an existing log; the live path decides in `quiet_renewal`."""
    last: dict[tuple[str, str], float] = {}
    out: list[Event] = []
    for ev in events:
        key = (ev.subject, str(ev.data.get("holder", ev.agent)))
        if ev.kind == "lease.acquired":
            last[key] = float(ev.data.get("at", 0.0))
        elif ev.kind == "lease.renewed" and set(ev.data) <= {"at", "holder", "schema"} and "at" in ev.data:
            at = float(ev.data["at"])
            if key in last and 0 <= at - last[key] < min_s:
                continue
            last[key] = at
        out.append(ev)
    return out

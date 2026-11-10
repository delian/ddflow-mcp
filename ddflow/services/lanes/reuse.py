"""Reuse a freed lane (LC-reuse): after release-at-enqueue, the lane takes the next item.

A release at enqueue (`services.lanerelease`) ends the agent's lease and tells its inbox it
may claim the next item (`lanerelease.ready_text`); the lanes view shows that freed lane as
idle-with-ready-work once ready work exists (`services.lanes.states`). This module is the one
place that turns a freed lane into its next move, and the baseline the phase tracks
(SW-baseline method): merged items per hour and lane idle time. It reads the ledger and the
log only; no daemon runs, and `ddflow next` plus the coordinator's lanes view are enough for
the MVP (D-daemons, r-lock-cycle-selfheal). `ddflow next --lane` scoring is a later
improvement, not part of this module.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from ...core.clock import epoch
from ...core.events import Event
from .. import lanerelease
from . import states as S
from .ledger import Lane

#: The event kind a merged item records: one per item that landed.
MERGED_KIND = "worktree.merged"
_SECONDS_PER_HOUR = 3600.0


def merged_per_hour(events: Iterable[Event], window_s: float, now: float) -> float:
    """Merged items per hour over the trailing ``window_s`` ending at ``now``: the
    ``worktree.merged`` events inside the window, scaled to an hour. ``now`` is the clock
    the window is measured from (the caller's), NOT the newest event, so a quiet lane decay
    to a smaller rate; ``idle_time_s`` takes the same argument. Pure over the log; a window
    <= 0 is refused so a caller never divides by it and never sees infinity."""
    if window_s <= 0:
        raise ValueError("window_s must be positive")
    evs = list(events)
    count = sum(
        1
        for e in evs
        if e.kind == MERGED_KIND and 0.0 <= now - epoch(e.ts) <= window_s
    )
    return count * (_SECONDS_PER_HOUR / window_s)


def idle_time_s(lane: Lane, now: float) -> float:
    """How long ``lane`` has sat idle since it was freed (its release, or its lease ending),
    or 0 while it works or is stopped. The measurement the phase tracks before/after
    (SW-baseline): a released lane that reclaims fast is a small number, a stuck one a large
    one."""
    if lane.state not in S.FREE:
        return 0.0
    return max(0.0, now - lane.since)


def next_for(lane: Lane, now: float) -> str:
    """The message a freed lane's next move reads: its idle time, the release message that
    freed it, and the exact call to take the next item. ``lane`` is the coordinator's ledger
    row (``services.lanes.find`` / ``ddflow lanes``), "" while the lane still holds an item."""
    if lane.state not in S.FREE:
        return ""  # still working or stopped: no next item to hand it
    return (
        f"lane {lane.name} has been idle for {idle_time_s(lane, now):g}s; claim the best "
        f"ready item with `ddflow next` (or MCP `ddflow_next`); it was freed by: "
        f"{lanerelease.ready_text(lane.item)}"
    )


def released(lanes: Sequence[Lane]) -> list[Lane]:
    """The lanes freed to idle (released or idle-with-ready-work): what ``ddflow lanes``
    shows as a released lane, in ledger order."""
    return [ln for ln in lanes if ln.state in S.FREE]

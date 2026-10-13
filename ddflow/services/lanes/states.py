"""The vocabulary of a lane's state, and the one rule that separates working from stale."""

from __future__ import annotations

WORKING = "working"
STALE = "stale"
IDLE = "idle"
IDLE_READY = "idle-with-ready-work"
WAIT_GLOB = "waiting-on-glob"
WAIT_DEP = "waiting-on-dep"
STOPPED_DONE = "stopped (done)"
STOPPED_ENDED = "stopped (session ended, item unfinished)"
#: The lease lapsed and the log does not say why: never "context exhausted" or "crash".
STOPPED_UNKNOWN = "stopped (reason unknown)"

ACTIVE = (WORKING, STALE)
STOPPED = (STOPPED_DONE, STOPPED_ENDED, STOPPED_UNKNOWN)
#: Stopped with its item unfinished: the lanes `advise` offers a resume for.
UNFINISHED = (STOPPED_ENDED, STOPPED_UNKNOWN)
FREE = (IDLE, IDLE_READY, WAIT_GLOB, WAIT_DEP)


def held_state(*, age_s: float, stale_after_s: float, lapsed: bool, session_ended: bool) -> str:
    """The state of a lane that holds a lease on an unfinished item.

    ``age_s`` is the time since its last heartbeat, ``lapsed`` whether the lease is past
    its TTL and grace (or its expiry is recorded) and ``session_ended`` whether the lane's
    session ended after its last heartbeat. Stale means "no heartbeat for ``stale_after_s``
    but the lease still holds"; an ended session or a lapsed lease is a stop.
    """
    if session_ended:
        return STOPPED_ENDED
    if lapsed:
        return STOPPED_UNKNOWN
    return STALE if age_s >= stale_after_s else WORKING

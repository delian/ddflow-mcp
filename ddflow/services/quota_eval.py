"""What a quota says right now, and whether work should pause (D-quotas-governor).

`evaluate` joins one subject's declared windows (`quota.Profile`) with the meters' readings
(`meters.best`) and says, per window, how much is used, how much that is of the limit, when
the window resets and whether the figure is stale. `decide` turns that into one answer: run,
or pause until a time. Both are pure: the clock, the profile and the readings come in as
arguments, so nothing here touches a store, an event log or a lease.

Three rules keep a measuring gap from reading as headroom or as an outage:

* an unlimited or unknown profile never pauses (an unknown one is the operator's to settle);
* a window nothing measures has no fraction and never pauses, but is listed, so the gap shows;
* a stale figure still counts when it is over the limit: pausing on old news is the safe side,
  and `resume` only resumes on a FRESH reading below the line.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

from ..core import clock
from . import meters, quota
from . import meters_base as B


@dataclass(frozen=True)
class WindowEval:
    window: str
    unit: str
    limit: float
    used: float | None  #: None: nothing measured it
    fraction: float | None
    resets_at: str  #: "" when neither the reading nor a fixed anchor says
    source: str
    stale: bool
    reason: str = ""  #: why there is no figure

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Decision:
    action: str  #: run | pause
    resume_at: float | None  #: epoch seconds; None when running
    windows: tuple[str, ...]  #: the windows at or over the line
    reason: str


def _fixed_reset(w: quota.Window, now: float) -> str:
    """The end of the FIXED window's current period, from its anchor; "" otherwise."""
    length = w.seconds()
    if w.reset != "fixed" or not w.anchor or not length:
        return ""
    start = clock.epoch(w.anchor, naive="refuse", default=math.nan)
    if math.isnan(start):  # an anchor that is not a time
        return ""
    periods = max(0, int((now - start) // length)) + 1
    return clock.iso_at(start + periods * length, timespec="seconds")


def _one(w: quota.Window, readings: Sequence[B.Reading], now: float, harness: str) -> WindowEval:
    r = meters.best(readings, w.window, w.unit, harness)
    resets = r.resets_at if r else ""
    if not resets or clock.epoch(resets, default=0.0) <= now:
        resets = _fixed_reset(w, now) or resets  # a reset already past is no help
    if r is None or r.status != B.OK or r.value is None:
        why = r.reason if r else "no meter measures this window"
        return WindowEval(
            w.window, w.unit, w.limit, None, None, resets, r.source if r else "", True, why
        )
    return WindowEval(
        w.window, w.unit, w.limit, r.value, r.value / w.limit, resets, r.source, r.stale
    )


def evaluate(
    profile: quota.Profile, readings: Sequence[B.Reading], now: float, harness: str = ""
) -> tuple[WindowEval, ...]:
    """Per declared window; empty for an unlimited or unknown profile (never pauses)."""
    if profile.state != "limited":
        return ()
    for w in profile.windows:
        if not w.limit > 0:
            raise ValueError(f"{w.window}: a limit must be positive (declare unlimited instead)")
    return tuple(_one(w, readings, now, harness) for w in profile.windows)


def _over(evals: Sequence[WindowEval], pause_at: float) -> list[WindowEval]:
    return [e for e in evals if e.fraction is not None and e.fraction >= pause_at]


def _resume_at(over: Sequence[WindowEval], now: float, recheck_s: float) -> float:
    """When to look again: the latest reset among the windows over the line, if every one of
    them names a reset still ahead; else ``recheck_s`` from now. A reset already past with
    the window still over the line is stale news, and waiting for it would loop at once."""
    resets = [clock.epoch(e.resets_at, default=0.0) for e in over if e.resets_at]
    if len(resets) < len(over) or min(resets) <= now:
        return now + recheck_s
    return max(resets)


def decide(evals: Sequence[WindowEval], pause_at: float, now: float, recheck_s: float) -> Decision:
    """Pause when any window is at or over ``pause_at`` (a fraction of its limit)."""
    if not 0 < pause_at <= 1:
        raise ValueError(f"pause_at must be above 0 and at most 1, got {pause_at!r}")
    if not recheck_s > 0:
        raise ValueError(f"recheck_s must be positive, got {recheck_s!r}")
    over = _over(evals, pause_at)
    if not over:
        if not evals:
            why = "no window limits this subject"
        elif any(e.fraction is not None for e in evals):
            why = "every measured window is below the line"
        else:
            why = "no window could be measured, so no limit is being checked"
        return Decision("run", None, (), why)
    names = tuple(e.window for e in over)
    worst = max(over, key=lambda e: e.fraction or 0.0)
    why = f"{worst.window} is at {worst.fraction:.0%} of its limit (pause at {pause_at:.0%})"
    return Decision("pause", _resume_at(over, now, recheck_s), names, why)


def resume(profile: quota.Profile, evals: Sequence[WindowEval], pause_at: float) -> bool:
    """May a paused subject resume? A profile that no longer limits (unlimited or unknown)
    never pauses, so yes. A limited one only when every window has a fresh figure below the
    line: a stale or missing figure is not evidence that the quota came back, and an empty
    list from a limited profile is a failed read, not an all-clear."""
    if profile.state != "limited":
        return True
    return bool(evals) and all(
        e.fraction is not None and not e.stale and e.fraction < pause_at for e in evals
    )

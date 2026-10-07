"""Time: the one place ddflow writes and reads timestamps (B-uni-clock).

Before this, four parsers and two writers disagreed on precision, on `Z` versus
`+00:00`, and on what a time with no zone means. They still disagree on that last point
-- a caller's meaning is kept, never guessed -- but now each one SAYS which it means:

- ``naive="utc"``: a zone-less time is UTC, as the log writes it (sessions, triggers,
  export queries);
- ``naive="local"``: it is the host's wall clock, Python's own reading (progress
  reports, record summaries, the compaction note);
- ``naive="refuse"``: it is an error, because the same wall-clock time is a different
  moment on another machine (quota anchors).

Failure values differ for the same reason (0.0, infinity, None, an exception), so every
reader takes the caller's ``default`` rather than a sentinel chosen here.

Pure (stdlib `datetime` only). The architecture guards count `fromisoformat` and
`strptime` outside this module and only let the count go down (D-unify 4).
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime
from typing import Literal

Naive = Literal["utc", "local", "refuse"]

#: What `parse_ts` refuses with: a time that is not one, or one of the wrong type.
UNPARSEABLE = (ValueError, TypeError, OverflowError, OSError)


def now_iso(*, timespec: Literal["microseconds", "seconds"] = "microseconds") -> str:
    """The current UTC time as the log writes it: ``2026-10-07T01:02:03.456789Z``, or to
    the second (``...T01:02:03Z``) where a record keeps that precision (quota samples)."""
    now = datetime.now(UTC)
    if timespec == "seconds":
        return now.strftime("%Y-%m-%dT%H:%M:%SZ")
    return now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_ts(text: str, *, naive: Naive = "utc", strip: bool = False) -> datetime:
    """An ISO-8601 time (`Z` or an offset, any fraction, or a bare date) as a datetime.

    Raises ValueError for text that is not a time, TypeError for a value that is not
    text. A zone-less time follows ``naive`` (module docstring); ``strip`` first trims
    surrounding whitespace, which most callers deliberately do not accept."""
    if not isinstance(text, str):
        raise TypeError(f"a timestamp is text, not {type(text).__name__}")
    t = datetime.fromisoformat((text.strip() if strip else text).replace("Z", "+00:00"))
    if t.tzinfo is None:
        if naive == "refuse":
            raise ValueError(f"{text!r} has no timezone")
        if naive == "utc":
            return t.replace(tzinfo=UTC)
    return t


def epoch(text: str, *, naive: Naive = "utc", default: float = 0.0) -> float:
    """`text` as epoch seconds, or ``default`` when it is not a time (any `UNPARSEABLE`)."""
    try:
        return parse_ts(text, naive=naive).timestamp()
    except UNPARSEABLE:
        return default


def age_s(text: str, now: float | None = None, *, naive: Naive = "utc") -> float:
    """Seconds from `text` to ``now`` (default: the current time); infinite when `text`
    is not a time, so an unreadable age is never young."""
    then = epoch(text, naive=naive, default=float("inf"))
    if then == float("inf"):
        return then
    return (time.time() if now is None else now) - then


def parse_date(text: str) -> date:
    """A calendar date in an ISO-8601 date form (``YYYY-MM-DD``; Python also reads the
    basic ``YYYYMMDD`` and week forms). Raises ValueError for anything else, a time
    included; a caller that holds a timestamp passes its first ten characters."""
    return date.fromisoformat(text)

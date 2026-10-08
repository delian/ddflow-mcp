"""Time: the one place ddflow parses a timestamp, and writes the log's (B-uni-clock).

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

Pure (stdlib `datetime`, and `secrets` for a run stamp's token). The architecture
guards count `fromisoformat` and `strptime` outside this module and only let the count
go down (D-unify 4).
"""

from __future__ import annotations

import math
import re
import secrets
import time
from datetime import UTC, date, datetime
from typing import Literal

Naive = Literal["utc", "local", "refuse"]

#: What `parse_ts` refuses with: a time that is not one, or one of the wrong type. A
#: TUPLE, for an ``except`` clause: unparseable text is always a ValueError.
UNPARSEABLE = (ValueError, TypeError, OverflowError, OSError)


class NoTimezone(ValueError):
    """A zone-less time where the caller asked for ``naive="refuse"``."""


Timespec = Literal["microseconds", "seconds"]


def now_utc() -> datetime:
    """The current time, timezone-aware, in UTC."""
    return datetime.now(UTC)


def _iso(when: datetime, timespec: Timespec) -> str:
    if timespec == "seconds":
        return when.strftime("%Y-%m-%dT%H:%M:%SZ")
    return when.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def now_iso(*, timespec: Timespec = "microseconds") -> str:
    """The current UTC time as the log writes it: ``2026-10-07T01:02:03.456789Z``, or to
    the second (``...T01:02:03Z``) where a record keeps that precision (quota samples)."""
    return _iso(now_utc(), timespec)


def iso_at(t: float, *, timespec: Timespec = "microseconds") -> str:
    """Epoch seconds ``t`` in the log's own form (`now_iso`'s), so a time held as a number
    (a lease's renewal) compares and sorts with the log's timestamps."""
    return _iso(datetime.fromtimestamp(t, UTC), timespec)


def compact_at(t: float | None = None) -> str:
    """Epoch seconds ``t`` (default: now) as a compact UTC stamp to the microsecond,
    ``20261007T010203456789Z``: sortable, and safe in a file name."""
    return datetime.fromtimestamp(time.time() if t is None else t, UTC).strftime("%Y%m%dT%H%M%S%fZ")


# -- Display: how a stored timestamp or a duration is SHOWN ------------------------------
#
# Pure text, never a parse: a stored timestamp is shown as written (its own zone, its own
# precision), only shortened. Each helper replaces the slicing that was repeated at its
# call sites (B-uni-clock.2-format); tests/test_clock_format.py pins them against it.


def fmt_minute(ts: str) -> str:
    """A stored timestamp to the minute, for a person: ``2026-10-07 01:02``; "" stays ""."""
    return ts[:16].replace("T", " ")


def fmt_date(ts: str) -> str:
    """The ``YYYY-MM-DD`` part of a stored timestamp; "" stays ""."""
    return ts[:10]


def fmt_time(t: float) -> str:
    """Epoch seconds as the UTC time of day, ``01:02:03Z``."""
    return datetime.fromtimestamp(t, UTC).strftime("%H:%M:%SZ")


#: The units `fmt_age` shows: seconds per unit, and the suffix written after the number.
AGE_UNITS: dict[str, tuple[int, str]] = {
    "m": (60, "m"),
    "min": (60, " min"),
    "h": (3600, "h"),
    "days": (86400, " days"),
}


def fmt_age(seconds: float, unit: str = "m", *, places: int = 0) -> str:
    """A duration in ``unit`` (a key of `AGE_UNITS`): whole units are FLOORED (``12m`` for
    12 min 59 s: a wait is never shown longer than it was), ``places`` > 0 rounds to that
    many decimals (``12.3m``, ``1.5 days``)."""
    per, suffix = AGE_UNITS[unit]
    if places:
        return f"{seconds / per:.{places}f}{suffix}"
    return f"{int(seconds // per)}{suffix}"


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
            raise NoTimezone(f"{text!r} has no timezone")
        if naive == "utc":
            return t.replace(tzinfo=UTC)
    return t


def epoch(text: str, *, naive: Naive = "utc", default: float = 0.0) -> float:
    """`text` as epoch seconds, or ``default`` when it is not a time (any `UNPARSEABLE`).
    With ``naive="refuse"`` a zone-less time is one of those: it too gives ``default``. A
    caller that must tell "no zone" from "not a time" calls `parse_ts` and catches
    `NoTimezone`, as quota does."""
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


#: The longest one `ddflow wait` may block, whoever asks (CLI or MCP). The client -- not
#: ddflow -- decides when a call has hung, so a longer ask is shortened and says so; the
#: caller that wants more waits again, which also re-checks that waiting is still the right
#: move. Here, in the leaf both surfaces may import (they reach services only through api).
WAIT_MAX_S = 1800

#: Seconds per unit in a duration text (`parse_duration`).
DURATION_UNITS: dict[str, int] = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 7 * 86400}
_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)([smhdw]?)")


def parse_duration(value: str | int | float, *, unit: str = "s") -> float:
    """A length of time in seconds: a number (in ``unit``, ``s`` by default) or text such as
    ``90``, ``30m``, ``1.5h`` or ``1h30m`` (units ``s m h d w``, largest first not
    required, a bare number in a compound taking the default unit only when alone).

    Raises ValueError for anything else -- negative, empty, a bool, an unknown unit --
    so a typo is refused rather than read as zero."""
    per = DURATION_UNITS.get(unit)
    if per is None:
        raise ValueError(f"unknown duration unit {unit!r}: use one of {', '.join(DURATION_UNITS)}")
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError(f"a duration is a number or text such as 30m, not {value!r}")
    if not isinstance(value, str):
        try:
            seconds = float(value) * per
        except OverflowError:
            seconds = math.inf  # an int beyond a float: refused below, as ``inf`` is
        return _finite(seconds, value)
    text = value.strip().lower().replace(" ", "")
    parts = _DURATION_PART.findall(text)
    if not text or "".join(n + u for n, u in parts) != text:
        raise ValueError(f"{value!r} is not a duration: use e.g. 90, 30m, 1.5h or 1h30m")
    if len(parts) > 1 and any(not u for _n, u in parts):
        raise ValueError(f"{value!r}: every part of a compound duration needs a unit")
    return _finite(sum(float(n) * (DURATION_UNITS[u] if u else per) for n, u in parts), value)


def _finite(seconds: float, value: object) -> float:
    """``seconds`` if it is a usable length of time; a ValueError naming ``value`` if not."""
    if seconds < 0 or not math.isfinite(seconds):
        raise ValueError(f"a duration cannot be {value!r}")
    return seconds


def parse_date(text: str) -> date:
    """A calendar date in an ISO-8601 date form (``YYYY-MM-DD``; Python also reads the
    basic ``YYYYMMDD`` and week forms). Raises ValueError for anything else, a time
    included; a caller that holds a timestamp passes its first ten characters."""
    return date.fromisoformat(text)


def run_stamp(at: float | None = None, *, token_hex: int = 8) -> str:
    """A name for one run, file or lock holder: its UTC second, then a fresh random token
    on every call -- ``20261007T010203Z-1a2b3c4d``. The time prefix sorts names made in
    different seconds chronologically on every machine (a local-time stamp sorted by
    each host's zone); within one second their order is arbitrary. The token keeps two
    made in the same second apart, which a time alone (seconds, or the nanoseconds two
    quick launches still shared) did not. ``at`` is epoch seconds (default: now);
    ``token_hex`` the token's length in hex digits."""
    when = datetime.fromtimestamp(time.time() if at is None else at, UTC)
    return f"{when.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(-(-token_hex // 2))[:token_hex]}"

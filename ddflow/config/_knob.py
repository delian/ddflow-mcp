"""The type and range of a numeric knob, checked when a value is loaded (Bc2f54fd0db).

A bool is an int in Python (`ttl_s = true` was a one-second lease), a float for a whole-number
knob (`max_rounds = 2.7`) was cut or crashed far from the file, and a negative, `inf` or `nan`
number turned a timer into nonsense. Every numeric knob is held to: the right kind of number
(a whole number for an integer knob), finite, and not below its minimum. The minimum is 0 for
a knob where 0 means "off" or "no limit" and 1 where 0 would break the thing (a lease that
lasts no time, a heartbeat that never beats), the latter listed in `MINIMUM_ONE`.

Pure: no environment, no files.
"""

from __future__ import annotations

import math
from typing import Any

#: Knobs where 0 is not a usable value, so the smallest allowed is 1 (every other numeric knob
#: takes 0 and up). Keyed "section.knob". A longer list is a sweep of the declarations, one
#: knob at a time, each checked against what reads it.
MINIMUM_ONE: frozenset[str] = frozenset(
    {
        "lease.ttl_s",
        "lease.heartbeat_s",
    }
)


#: Knobs whose own doc gives a value of 0 OR LESS a meaning ("0 or less refuses every ask",
#: "0 or less skips the work"), so no floor applies: they still must be finite numbers.
NO_FLOOR: frozenset[str] = frozenset(
    {"agent.ask_ttl_s", "upgrade.start_timeout_s", "review.degraded_cooldown_s"}
)


def _kind(ts: str) -> str:
    """ "int", "float" or "" for a type spelling that is exactly a number (optionally Optional)."""
    t = ts.replace(" ", "").replace("typing.", "")
    for name in ("int", "float"):
        if t in (name, f"Optional[{name}]", f"{name}|None", f"None|{name}"):
            return name
    return ""


def kind(ts: str) -> str:
    """The numeric kind ("int", "float") of the type spelling ``ts``, or "" when it is not one."""
    return _kind(ts)


def allowed(key: str, kind: str) -> str:
    """What a value for ``key`` may be, in words."""
    what = "a whole number" if kind == "int" else "a finite number"
    if key in NO_FLOOR:
        return what
    return f"{what} >= {1 if key in MINIMUM_ONE else 0}"


def checked_number(key: str, raw: Any, value: Any, ts: str) -> Any:
    """``value`` (what the knob will hold) if it is an allowed number for the knob typed
    ``ts``, else a `ValueError` naming ``raw``, the value as written, and what is allowed.

    A whole-number float for an integer knob (`7200.0`) loads as the integer. A value that is
    not a number at all is left to the caller's own type handling."""
    kind = _kind(ts)
    if not kind or value is None:
        return value
    why = ""
    if isinstance(value, bool):
        why = "a boolean is not a number"
    elif not isinstance(value, int | float):
        why = f"it is {type(value).__name__}, not a number"
    elif isinstance(value, float) and not math.isfinite(value):
        why = "it is not finite"
    elif kind == "int" and isinstance(value, float) and not value.is_integer():
        why = "it is not a whole number"
    elif key not in NO_FLOOR and value < (1 if key in MINIMUM_ONE else 0):
        why = "it is below the minimum"
    if why:
        raise ValueError(f"{why}; allowed: {allowed(key, kind)}")
    return int(value) if kind == "int" and isinstance(value, float) else value

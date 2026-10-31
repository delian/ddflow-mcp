"""How a redaction fails: loudly for a bad pattern, closed for anything else.

A pattern that does not compile is a configuration error (`PatternError`): raised, nothing
is written. Any OTHER failure of a redaction must never let the raw text through: the
writers replace the text with `FAILED_MARKER` (and count that as one redaction).
"""

from __future__ import annotations

from collections.abc import Mapping

__all__ = [
    "FAILED_MARKER",
    "PatternError",
    "fail_closed_data",
    "fail_closed_text",
]


class PatternError(ValueError):
    """A redaction pattern that does not compile (a configuration error)."""


#: What replaces a text whose redaction itself failed.
FAILED_MARKER = "[REDACTED:redaction-failed]"


def fail_closed_text() -> tuple[str, int]:
    """(placeholder, count) for a text whose redaction raised unexpectedly."""
    return FAILED_MARKER, 1


def fail_closed_data(value: object) -> object:
    """``value`` with every string leaf replaced by `FAILED_MARKER`; keys, numbers, booleans and None kept."""
    if isinstance(value, str):
        return FAILED_MARKER
    if isinstance(value, Mapping):
        return {k: fail_closed_data(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [fail_closed_data(v) for v in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return FAILED_MARKER  # an unknown type may hold text: never let it through

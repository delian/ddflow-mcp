"""The one adapter for the optional ``[search]`` extra (rapidfuzz), with its stdlib fallback.

Nothing else imports rapidfuzz (the `extras-one-adapter` contract). ``ratio`` is rapidfuzz's
token-set ratio when the extra is installed and ``difflib``'s sequence ratio otherwise; both
are 0..1. ``available`` says which one a caller is getting, for the doctor line.
"""

from __future__ import annotations

import difflib

try:  # the [search] extra; every caller works without it
    from rapidfuzz import fuzz as _fuzz
except ImportError:  # pragma: no cover - depends on the environment
    _fuzz = None


def available() -> bool:
    """Is the ``[search]`` extra (rapidfuzz) installed?"""
    return _fuzz is not None


def ratio(a: str, b: str) -> float:
    """0..1 fuzzy likeness of two strings."""
    a, b = a.lower(), b.lower()  # both backends compare without case
    if _fuzz is not None:
        return _fuzz.token_set_ratio(a, b) / 100.0
    return difflib.SequenceMatcher(None, a, b).ratio()

"""The `Fuzzy` port and its default adapter: fuzzy string likeness.

The ONE home for the optional ``[search]`` extra (rapidfuzz), with the stdlib `difflib`
fallback (`extras-one-adapter`): nothing else imports rapidfuzz. ``ratio`` is rapidfuzz's
token-set ratio when the extra is installed and ``difflib``'s sequence ratio otherwise; both
are 0..1 and folded to lowercase. ``available`` says which one a caller is getting. `core`
re-exports the functions through ``ddflow/core/fuzzy.py`` (``core`` stays pure, no
third-party import).
"""

from __future__ import annotations

import difflib
from typing import Protocol, runtime_checkable

try:  # the [search] extra; every caller works without it
    from rapidfuzz import fuzz as _fuzz
except ImportError:  # pragma: no cover - depends on the environment
    _fuzz = None

from .ports import port

__all__ = ["DEFAULT", "DefaultFuzzy", "Fuzzy"]


def _likeness(a: str, b: str, fuzz) -> float:
    """0..1 likeness via ``fuzz`` (rapidfuzz's token set; None -> `difflib`)."""
    a, b = a.lower(), b.lower()  # both backends compare without case
    if fuzz is not None:
        return fuzz.token_set_ratio(a, b) / 100.0
    return difflib.SequenceMatcher(None, a, b).ratio()


@port
@runtime_checkable
class Fuzzy(Protocol):
    """Fuzzy string likeness. Home: `ddflow.adapters.fuzzy` (`DefaultFuzzy`); fake `FakeFuzzy`."""

    def ratio(self, a: str, b: str) -> float:
        """0..1 fuzzy likeness of two strings (case-insensitive)."""

    def available(self) -> bool:
        """Is the ``[search]`` extra (rapidfuzz) installed?"""


class DefaultFuzzy:
    """The default adapter: rapidfuzz when installed, `difflib`'s sequence ratio otherwise."""

    def ratio(self, a: str, b: str) -> float:
        return _likeness(a, b, _fuzz)

    def available(self) -> bool:
        return _fuzz is not None


#: The one stateless instance callers default to; a test passes `FakeFuzzy` instead.
DEFAULT = DefaultFuzzy()

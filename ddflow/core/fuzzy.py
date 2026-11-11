"""Re-export: the ONE fuzzy adapter lives in `ddflow.adapters.fuzzy` (the rapidfuzz home).

`core` stays pure: it imports no third-party package directly. The rapidfuzz/difflib
``DefaultFuzzy`` adapter (the ONE implementation of `ratio`, in `ddflow.adapters.fuzzy`)
lives at the bottom; this module re-binds its `_fuzz` (the imported rapidfuzz module, or
``None``) and exposes ``ratio``/``available`` reading THIS ``_fuzz``, so `from
ddflow.core import fuzzy`, then ``fuzzy.ratio(...)``, keeps working and a test can re-point
``fuzzy._fuzz`` at a stand-in.
"""

from __future__ import annotations

from ..adapters.fuzzy import _fuzz, _likeness

__all__ = ["available", "ratio"]


def available() -> bool:
    """Is the ``[search]`` extra (rapidfuzz) installed?"""
    return _fuzz is not None


def ratio(a: str, b: str) -> float:
    """0..1 fuzzy likeness of two strings (case-insensitive)."""
    return _likeness(a, b, _fuzz)

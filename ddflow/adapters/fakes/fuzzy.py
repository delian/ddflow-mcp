"""`FakeFuzzy`: a deterministic `Fuzzy` that needs no rapidfuzz (case-folded exact match)."""

from __future__ import annotations

from . import fake


@fake
class FakeFuzzy:
    """Identical strings (any case) score 1.0; different ones 0.0. `available` is always True."""

    def ratio(self, a: str, b: str) -> float:
        return 1.0 if a.lower() == b.lower() else 0.0

    def available(self) -> bool:
        return True

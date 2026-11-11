"""Re-export: the ONE fuzzy adapter lives in `ddflow.adapters.fuzzy` (the rapidfuzz home).

`core` stays pure: it imports no third-party package directly (`core must not import a
third-party package`, ADP-thirdparty-singles). The rapidfuzz/difflib adapter lives at the
bottom (`ddflow.adapters.fuzzy`), and this module re-exports the same `ratio` and
`available`, so `from ddflow.core import fuzzy`, then `fuzzy.ratio(...)`, keeps working.
"""

from __future__ import annotations

from ..adapters.fuzzy import available, ratio

__all__ = ["available", "ratio"]

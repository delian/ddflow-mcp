"""One search engine with pluggable sources (B-uni-search-core; D-unify 3).

`hit` is the one row type and the source registry, `rank` the rankers, `regexsafe` the
regex check every mode shares. `services/search.py` is the caller behind `ddflow search`.
"""

from __future__ import annotations

from .hit import FuncSource, Hit, SearchSource, gather, register, registered
from .rank import bm25, rrf, tfidf
from .regexsafe import SearchError, check_regex

__all__ = [
    "FuncSource",
    "Hit",
    "SearchError",
    "SearchSource",
    "bm25",
    "check_regex",
    "gather",
    "register",
    "registered",
    "rrf",
    "tfidf",
]

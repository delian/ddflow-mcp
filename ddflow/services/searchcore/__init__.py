"""One search engine with pluggable sources (B-uni-search-core; D-unify 3).

`hit` is the one row type and the source registry, `rank` the rankers, `indexed` the index tables
that `recall` and the lesson and decision searches rank, `regexsafe` the regex check for the modes that
take a pattern. `services/search.py` is the caller behind `ddflow search`.
"""

from __future__ import annotations

from .hit import FuncSource, Hit, SearchSource, gather, register, registered
from .indexed import matcher, search_sources, search_table
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
    "matcher",
    "register",
    "registered",
    "rrf",
    "search_sources",
    "search_table",
    "tfidf",
]

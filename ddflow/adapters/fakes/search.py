"""`FakeSearchIndex`: an in-memory `SearchIndex` that needs no SQLite.

Its rows and postings live in plain lists and dicts on the instance, so a test (or a
downstream user without the ``[rag]`` extra) exercises the `SearchIndex` port without a
database. ``sqlite3`` stays in ``ddflow.adapters.search`` (the ``sqlite-home`` contract).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ..search import Postings, SearchDoc
from . import fake


@fake
class FakeSearchIndex:
    """A `SearchIndex` whose state is the caller's data, held here untouched."""

    def __init__(self) -> None:
        self.docs: list[SearchDoc] = []
        self.postings: dict[str, Postings] = {}

    def create(self, con) -> None:  # no tables to create; the instance is the store
        return None

    def rebuild(self, con, docs: Sequence[SearchDoc], postings: Mapping[str, Postings]) -> None:
        self.docs = list(docs)
        self.postings = dict(postings)

    def index(self, con, docs: Sequence[SearchDoc], postings: Mapping[str, Postings]) -> None:
        # `index` writes the COMPLETE corpus into an EMPTY state (no incremental append,
        # as the store's `_insert_similar` has none); `rebuild` is the same write.
        self.docs = list(docs)
        self.postings = dict(postings)

    def query(self, con, terms: Sequence[str]) -> dict[str, Postings]:
        return {t: self.postings[t] for t in terms if t in self.postings}

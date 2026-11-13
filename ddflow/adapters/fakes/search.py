"""`FakeSearchIndex`: an in-memory `SearchIndex` that needs no SQLite.

Its rows and postings live in plain lists and dicts on the instance, so a test (or a
downstream user without the ``[rag]`` extra) exercises the `SearchIndex` port without a
database. ``sqlite3`` stays in ``ddflow.adapters.indexdb`` (the ``sqlite-home`` contract).
"""

from __future__ import annotations

from array import array
from collections.abc import Mapping, Sequence

from ..search import Postings, SearchDoc
from . import fake


@fake
class FakeSearchIndex:
    """A `SearchIndex` whose state is the caller's data, held here. ``index`` adds a batch
    of `docs` and `postings` (their ordinals are offset past what is already held); a
    partial replace is `rebuild`."""

    def __init__(self) -> None:
        self.docs: list[SearchDoc] = []
        self.postings: dict[str, Postings] = {}

    def create(self, con) -> None:  # no tables to create; the instance is the store
        return None

    def rebuild(self, con, docs: Sequence[SearchDoc], postings: Mapping[str, Postings]) -> None:
        self.docs = list(docs)
        self.postings = dict(postings)

    def index(self, con, docs: Sequence[SearchDoc], postings: Mapping[str, Postings]) -> None:
        start = len(self.docs)
        self.docs.extend(docs)
        for term, (ords, weights) in postings.items():
            off = ords if not start else array(ords.typecode, (start + o for o in ords))
            held = self.postings.get(term)
            if held is None:
                self.postings[term] = (off, weights)
            else:  # a term already indexed gains the batch's ordinals and weights beside its own
                held[0].extend(off)
                held[1].extend(weights)

    def query(self, con, terms: Sequence[str]) -> dict[str, Postings]:
        return {t: self.postings[t] for t in terms if t in self.postings}

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
    """A `SearchIndex` whose state is the caller's data, held here. `index` writes the
    COMPLETE corpus into an empty store's place; `rebuild` is the same write. The arrays
    this fake holds are its OWN copies, never the caller's."""

    def __init__(self) -> None:
        self.docs: list[SearchDoc] = []
        self.postings: dict[str, Postings] = {}

    def create(self, con) -> None:  # no tables to create; the instance is the store
        return None

    def rebuild(self, con, docs: Sequence[SearchDoc], postings: Mapping[str, Postings]) -> None:
        self.docs = list(docs)
        self.postings = _owned(postings)

    def index(self, con, docs: Sequence[SearchDoc], postings: Mapping[str, Postings]) -> None:
        self.docs = list(docs)
        self.postings = _owned(postings)

    def query(self, con, terms: Sequence[str]) -> dict[str, Postings]:
        # A COPY: a caller captured before a later `index` must keep the snapshot it saw
        # (the SQLite default reads fresh arrays each call; the fake does the same here).
        return {
            t: (
                array(self.postings[t][0].typecode, self.postings[t][0]),
                array(self.postings[t][1].typecode, self.postings[t][1]),
            )
            for t in terms
            if t in self.postings
        }


def _owned(postings: Mapping[str, Postings]) -> dict[str, Postings]:
    """`postings` with a COPY of every array, so no stored array aliases the caller's."""
    return {t: (array(o.typecode, o), array(w.typecode, w)) for t, (o, w) in postings.items()}

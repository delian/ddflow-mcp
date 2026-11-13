"""The `SearchIndex` port and its default adapter: the SQLite-backed search index.

The search index the similarity engine reads (`infra.store`'s `similar_*` tables, the
"[rag] sqlite-vec" half of ``D-unify-r2``): one row per indexed record and, per term, its
postings (record ordinals and TF-IDF weights, packed as two ``array`` blobs). Tokenizing
and weighting stay in ``core.textsim`` -- the one tokenizer both a stored vector and a
fresh query use, which is what keeps them comparable -- so this port is the SQLite
storage and retrieval seam and nothing else: a caller reaches the index through
``ddflow.adapters.indexdb`` (the ``sqlite-home`` contract, ``D-unify-r2``) instead of
importing ``sqlite3`` here too. ``index`` writes the COMPLETE corpus into an empty
index; ``rebuild`` clears the whole index and writes it; ``query`` reads the postings of
the terms asked. There is no incremental append: the store's ``_insert_similar`` writes
the whole corpus at once, so a partial list would leave the records it omits behind.
"""

from __future__ import annotations

from array import array
from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

from .indexdb import Connection
from .ports import port

__all__ = ["DEFAULT", "Postings", "SearchDoc", "SearchIndex", "SqliteSearchIndex"]

#: One term's postings: the record ordinals holding it and its weight in each. Both pack
#: as ``array`` blobs, the ordinals with typecode ``"q"`` and the weights with ``"d"`` --
#: the two typecodes ``core.textsim.invert`` produces, and the ONLY valid ones. A `Postings`
#: built with another typecode is refused at write time (``_packed``), never stored as a blob
#: ``query`` would read back with the wrong width.
Postings = tuple[array, array]
#: One indexed record's stored row: id, kind, item, digest (the ``similar_docs`` columns;
#: the digest is ``core.textsim.digest`` of its title and body, computed by the caller).
SearchDoc = tuple[str, str, str, str]

#: The fixed typecodes of `Postings` (``array("q")`` ordinals, ``array("d")`` weights),
#: shared by the write and read paths so they cannot drift apart.
_ORD_TYPECODE = "q"
_W_TYPECODE = "d"

_DOCS = "create table if not exists similar_docs(ord integer primary key, id text, kind text, item text, digest text)"
_TERMS = "create table if not exists similar_terms(term text primary key, docs blob, weights blob) without rowid"


@port
@runtime_checkable
class SearchIndex(Protocol):
    """The SQLite-backed search index. Home: `ddflow.adapters.search` (`SqliteSearchIndex`);
    fake `FakeSearchIndex`."""

    def create(self, con: Connection) -> None:
        """Create the ``similar_docs`` / ``similar_terms`` tables if they are absent."""

    def rebuild(
        self, con: Connection, docs: Sequence[SearchDoc], postings: Mapping[str, Postings]
    ) -> None:
        """Replace the WHOLE index with ``docs`` and ``postings`` (the COMPLETE current
        corpus), removing every previous row first."""

    def index(
        self, con: Connection, docs: Sequence[SearchDoc], postings: Mapping[str, Postings]
    ) -> None:
        """Write ``docs`` and ``postings``, the COMPLETE current corpus, into an EMPTY
        index (the ordinals are over ``docs``). `rebuild` clears first."""

    def query(self, con: Connection, terms: Sequence[str]) -> dict[str, Postings]:
        """The postings of ``terms``, for the terms the index holds."""


def _packed(ords: array, weights: array) -> tuple[bytes, bytes]:
    """``(ords_bytes, weights_bytes)`` for the write, refusing a typecode other than
    ``"q"``/``"d"``."""
    if ords.typecode != _ORD_TYPECODE or weights.typecode != _W_TYPECODE:
        raise ValueError(
            f"postings pack ordinals as array({_ORD_TYPECODE!r}) and weights as "
            f"array({_W_TYPECODE!r}) (core.textsim.invert's); got "
            f"array({ords.typecode!r})/array({weights.typecode!r})"
        )
    return ords.tobytes(), weights.tobytes()


class SqliteSearchIndex:
    """The default adapter: the ``similar_docs`` / ``similar_terms`` tables in a SQLite database.

    ``sqlite3`` stays in ``ddflow.adapters.indexdb`` (the ``sqlite-home`` contract); a
    caller passes the connection (disk or ``:memory:``) and this class does only the
    read/write through it."""

    def create(self, con: Connection) -> None:
        con.execute(_DOCS)
        con.execute(_TERMS)

    def rebuild(
        self, con: Connection, docs: Sequence[SearchDoc], postings: Mapping[str, Postings]
    ) -> None:
        self.create(con)
        # Validate first: a bad `postings` must not remove the index it will not replace.
        rows = [(t, *_packed(ords, weights),) for t, (ords, weights) in postings.items()]
        con.execute("delete from similar_docs")
        con.execute("delete from similar_terms")
        self._write(con, docs, rows)

    def index(
        self, con: Connection, docs: Sequence[SearchDoc], postings: Mapping[str, Postings]
    ) -> None:
        # Validate first: a bad `postings` must not leave docs with no postings written.
        rows = [(t, *_packed(ords, weights),) for t, (ords, weights) in postings.items()]
        self._write(con, docs, rows)

    def _write(self, con: Connection, docs: Sequence[SearchDoc], rows) -> None:
        con.executemany(
            "insert into similar_docs values(?,?,?,?,?)",
            [(i, d[0], d[1], d[2], d[3]) for i, d in enumerate(docs)],
        )
        con.executemany("insert into similar_terms values(?,?,?)", rows)

    def query(self, con: Connection, terms: Sequence[str]) -> dict[str, Postings]:
        out: dict[str, Postings] = {}
        # In chunks: SQLite caps bound parameters (999 on older builds), and a long body
        # has more distinct terms than that.
        for k in range(0, len(terms), 500):
            chunk = list(terms[k : k + 500])
            ph = ",".join("?" * len(chunk))
            # bandit B608: only `?` placeholders are interpolated.
            sql = f"select term, docs, weights from similar_terms where term in ({ph})"  # nosec B608
            for term, docs, weights in con.execute(sql, chunk):
                ords, wts = array(_ORD_TYPECODE), array(_W_TYPECODE)
                ords.frombytes(docs)
                wts.frombytes(weights)
                out[term] = (ords, wts)
        return out


#: The one stateless instance callers default to; a test passes the fake instead.
DEFAULT = SqliteSearchIndex()

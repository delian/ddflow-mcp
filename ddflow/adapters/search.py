"""The `SearchIndex` port and its default adapter: the SQLite-backed search index.

The search index the similarity engine reads (`infra.store`'s `similar_*` tables, the
"[rag] sqlite-vec" half of ``D-unify-r2``): one row per indexed record and, per term, its
postings (record ordinals and TF-IDF weights, packed as two ``array`` blobs). Tokenizing
and weighting stay in ``core.textsim`` -- the one tokenizer both a stored vector and a
fresh query use, which is what keeps them comparable -- so this port is the SQLite
storage and retrieval seam and nothing else: a caller reaches the index through
``ddflow.adapters.indexdb`` (the ``sqlite-home`` contract, ``D-unify-r2``) instead of
importing ``sqlite3`` here too. ``index`` adds documents and their postings to the
index; ``rebuild`` replaces the whole index; ``query`` reads the postings of the terms
asked.
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
        corpus). Any previous rows are removed first, so a repeated rebuild of a partial
        list is safe, as it would not be for `index`."""

    def index(
        self, con: Connection, docs: Sequence[SearchDoc], postings: Mapping[str, Postings]
    ) -> None:
        """Add ``docs`` and ``postings`` to the index (their ordinals are over ``docs`` and
        are offset past rows already indexed). `rebuild` replaces the whole index."""

    def query(self, con: Connection, terms: Sequence[str]) -> dict[str, Postings]:
        """The postings of ``terms``, for the terms the index holds."""


def _packed(ords: array, weights: array, start: int) -> tuple[bytes, bytes]:
    """``(ords_bytes, weights_bytes)`` for the write, refusing a typecode other than
    ``"q"``/``"d"`` and offsetting the ordinals past ``start`` already-indexed rows."""
    if ords.typecode != _ORD_TYPECODE or weights.typecode != _W_TYPECODE:
        raise ValueError(
            f"postings pack ordinals as array({_ORD_TYPECODE!r}) and weights as "
            f"array({_W_TYPECODE!r}) (core.textsim.invert's); got "
            f"array({ords.typecode!r})/array({weights.typecode!r})"
        )
    off = ords if not start else array(_ORD_TYPECODE, (start + o for o in ords))
    return off.tobytes(), weights.tobytes()


def _packed_rows(postings: Mapping[str, Postings], start: int) -> list[tuple[str, bytes, bytes]]:
    """Every term's ``(term, ords_bytes, weights_bytes)``, validating ALL of them up
    front so an invalid `postings` raises before the caller writes or clears anything."""
    out = []
    for term, (ords, weights) in postings.items():
        ords_bytes, weights_bytes = _packed(ords, weights, start)
        out.append((term, ords_bytes, weights_bytes))
    return out


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
        rows = _packed_rows(postings, 0)
        con.execute("delete from similar_docs")
        con.execute("delete from similar_terms")
        con.executemany(
            "insert into similar_docs values(?,?,?,?,?)",
            [(i, d[0], d[1], d[2], d[3]) for i, d in enumerate(docs)],
        )
        con.executemany("insert into similar_terms values(?,?,?)", rows)

    def index(
        self, con: Connection, docs: Sequence[SearchDoc], postings: Mapping[str, Postings]
    ) -> None:
        start = con.execute("select count(*) from similar_docs").fetchone()[0]
        # Validate first: a bad `postings` must not leave docs with no postings written.
        rows = _packed_rows(postings, start)
        con.executemany(
            "insert into similar_docs values(?,?,?,?,?)",
            [(start + i, d[0], d[1], d[2], d[3]) for i, d in enumerate(docs)],
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

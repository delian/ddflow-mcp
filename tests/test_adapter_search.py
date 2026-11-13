"""The `SearchIndex` port: the default adapter's behaviour and the fake's (CONFORMANCE)."""

from __future__ import annotations

from array import array

from ddflow.adapters import search as N
from ddflow.adapters.fakes import FAKES
from ddflow.adapters.fakes.search import FakeSearchIndex
from ddflow.adapters.indexdb import connect_memory
from ddflow.adapters.search import SearchIndex

_DOCS = [("a", "task", "", "d1"), ("b", "bug", "a", "d2")]
_POSTINGS = {
    "hello": (array("q", [0, 1]), array("d", [0.5, 0.5])),
    "world": (array("q", [1]), array("d", [1.0])),
}


def search_conformance(idx: SearchIndex) -> None:
    con = connect_memory()
    idx.create(con)
    idx.rebuild(con, _DOCS, _POSTINGS)
    hit = idx.query(con, ["hello", "missing"])
    assert tuple(hit["hello"][0].tolist()) == (0, 1)
    assert tuple(round(w, 9) for w in hit["hello"][1].tolist()) == (0.5, 0.5)
    assert "missing" not in hit
    con.close()


CONFORMANCE = {"SearchIndex": {"real": lambda monkeypatch: N.SqliteSearchIndex(), "cases": search_conformance}}


def test_the_adapter_and_the_fake_satisfy_the_port():
    assert isinstance(N.SqliteSearchIndex(), SearchIndex)
    assert isinstance(FakeSearchIndex(), SearchIndex)
    assert FAKES["SearchIndex"] is FakeSearchIndex


def test_the_default_adapter_round_trips_postings_through_sqlite():
    con = connect_memory()
    idx = N.SqliteSearchIndex()
    idx.create(con)
    idx.index(con, _DOCS, _POSTINGS)
    got = idx.query(con, ["world"])
    assert tuple(got["world"][0].tolist()) == (1,)
    assert tuple(round(w, 9) for w in got["world"][1].tolist()) == (1.0,)
    con.close()


def test_the_fake_keeps_the_callers_data():
    idx = FakeSearchIndex()
    idx.rebuild(None, _DOCS, _POSTINGS)
    assert idx.docs == list(_DOCS)
    assert dict(idx.postings) == _POSTINGS
    assert idx.query(None, ["hello"]) == {"hello": _POSTINGS["hello"]}

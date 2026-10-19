"""The roadmap's phase dependency graph (B-rm-graph): reduced edges, lane classes, the cap."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_export_docs_a import Log, _body

from ddflow.services.export import Filters, registry
from ddflow.services.export import roadmap_graph as RG

CHAIN = {"B": ["A"], "C": ["A", "B"]}


def data(log: Log, **f) -> dict:
    return registry.get("roadmap").data(log.query(), Filters(**f))


def test_edges_implied_by_a_longer_path_are_removed():
    assert RG.reduce_edges(["A", "B", "C"], lambda i: CHAIN.get(i, [])) == [("A", "B"), ("B", "C")]


def test_a_cycle_keeps_its_edges_and_a_stranger_is_ignored():
    cyc = {"A": ["B", "ZZ"], "B": ["A"]}
    assert sorted(RG.reduce_edges(["A", "B"], lambda i: cyc.get(i, []))) == [("A", "B"), ("B", "A")]


def test_the_cap_leaves_phases_off_and_counts_them():
    ph = [{"id": i, "lane": "next"} for i in "ABCD"]
    g = RG.graph(ph, lambda i: CHAIN.get(i, []), 3)
    assert [n["id"] for n in g["nodes"]] == ["A", "B", "C"] and g["more"] == 1
    assert RG.graph(ph, lambda i: [], 0) == {"nodes": [], "edges": [], "more": 4}


def test_node_names_are_safe_and_never_collide():
    ph = [{"id": "P-unify.1", "lane": "now"}, {"id": "P-unify-1", "lane": "now"}]
    keys = [n["key"] for n in RG.graph(ph, lambda i: [], 5)["nodes"]]
    assert keys == ["n0", "n1"]


def test_mutually_dependent_prerequisites_keep_their_edges_into_a_third_phase():
    deps = {"i": ["d", "o"], "d": ["o"], "o": ["d"]}
    edges = RG.reduce_edges(["d", "o", "i"], lambda x: deps.get(x, []))
    assert ("d", "i") in edges and ("o", "i") in edges


def _log() -> Log:
    g = Log()
    g.phase("P1", "first", 10)
    g.task("T1", "P1", "a")
    g.run("T1", "2026-01-01T00:00:00Z")
    g.phase("P2", "second", 20, needs=["P1"])
    g.task("T2", "P2", "b")
    g.phase("P3", "third", 30, needs=["P1", "P2"])
    g.task("T3", "P3", "c")
    return g


def test_the_data_carries_lanes_and_the_reduced_edges():
    gr = data(_log())["graph"]
    assert {n["id"]: n["lane"] for n in gr["nodes"]} == {"P1": "now", "P2": "later", "P3": "later"}
    assert gr["edges"] == [["n0", "n1"], ["n1", "n2"]] and gr["more"] == 0


def test_the_document_draws_a_mermaid_flowchart_with_classes():
    body = _body("roadmap", _log().query())
    assert "```mermaid\nflowchart LR" in body
    assert 'n0["P1"]:::now' in body and "n1 --> n2" in body and "n0 --> n2" not in body
    assert "classDef later" in body


def test_the_knob_is_validated_with_the_other_roadmap_keys():
    import pytest

    from ddflow.config import Config

    Config.check({"export": {"roadmap": {"graph": 0}}})
    with pytest.raises(ValueError, match="graph"):
        Config.check({"export": {"roadmap": {"graph": -1}}})


def test_an_edge_into_a_cycle_is_not_dropped_by_a_path_through_itself():
    deps = {"b": ["i"], "i": ["a", "b"]}
    edges = RG.reduce_edges(["a", "b", "i"], lambda i: deps.get(i, []))
    assert ("a", "i") in edges and ("i", "b") in edges and ("b", "i") in edges


def test_a_phase_in_a_cycle_keeps_all_its_edges_whatever_reaches_what():
    deps = {"a": [], "b": ["i", "a"], "i": ["a", "b"]}
    edges = RG.reduce_edges(["a", "b", "i"], lambda x: deps[x])
    assert sorted(edges) == [("a", "b"), ("a", "i"), ("b", "i"), ("i", "b")]

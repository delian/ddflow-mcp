"""The search core (services/searchcore) and the callers that moved behind it."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli


def test_rule_add_refuses_an_id_that_could_not_be_read_back(repo):
    """B814dc6df7a: `rule add --id R-style` said 'added' and wrote a file no loader accepts,
    so the rule vanished from list and search."""
    run_cli(repo, "init")
    code, _out, err = run_cli(
        repo, "rule", "add", "--id", "R-style", "--title", "Keep it short",
        "--content", "small functions", "--new",
    )  # fmt: skip
    assert code != 0
    assert "kebab-case" in err
    assert not (repo / ".ddflow" / "rules" / "R-style.toml").exists()


def _legacy_bm25(docs, q):
    """The formula skills.py carried before it moved behind the core."""
    import math

    n = len(docs)
    avg = (sum(len(d) for d in docs) / n) or 1.0
    df = {t: sum(1 for d in docs if t in d) for t in q}
    out = {}
    for i, d in enumerate(docs):
        score = 0.0
        for t in q:
            tf = d.count(t)
            if not tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * tf * 2.5 / (tf + 1.5 * (0.25 + 0.75 * len(d) / avg))
        if score > 0:
            out[i] = score
    return out


def test_bm25_is_the_formula_skills_used():
    from ddflow.services.searchcore import bm25

    docs = [["deploy", "release", "deploy"], ["write", "test"], ["release"], []]
    for q in (["deploy"], ["release", "test"], ["zzz"], ["deploy", "write", "release"]):
        assert bm25(docs, q) == _legacy_bm25(docs, set(q))
    assert bm25([], ["a"]) == {}
    assert bm25(docs, []) == {}


def test_rrf_fuses_rankings_and_ties_break_by_id():
    from ddflow.services.searchcore import rrf
    from ddflow.services.searchcore.rank import best_first

    fused = rrf([["a", "b", "c"], ["b", "a"]])
    assert fused["a"] == fused["b"] > fused["c"]
    assert best_first(fused) == ["a", "b", "c"]
    assert rrf([]) == {}


def test_the_old_import_path_still_serves_the_regex_check():
    from ddflow.services import search as S
    from ddflow.services import searchcore as C

    assert S.check_regex is C.check_regex
    assert S.SearchError is C.SearchError
    assert S.Doc is C.Hit


def test_sources_register_and_gather_only_the_kinds_asked_for():
    from ddflow.services.searchcore import FuncSource, Hit, gather
    from ddflow.services.searchcore import hit as H

    saved = dict(H._REGISTRY)
    try:
        H._REGISTRY.clear()
        H.register(FuncSource("a", ("x",), lambda ctx, kinds: [Hit("x", ctx, "", "", "", "", "t")]))
        H.register(FuncSource("b", ("y",), lambda ctx, kinds: [Hit("y", ctx, "", "", "", "", "t")]))
        assert [h.kind for h in gather("1", {"x", "y"})] == ["x", "y"]
        assert [h.kind for h in gather("1", {"y"})] == ["y"]
        assert gather("1", {"z"}) == []
    finally:
        H._REGISTRY.clear()
        H._REGISTRY.update(saved)


def test_tfidf_ranks_the_closest_doc_first_and_omits_docs_sharing_no_term():
    from ddflow.services.searchcore import tfidf

    docs = [["alpha", "beta"], ["gamma"], ["alpha"], []]
    got = tfidf(docs, ["alpha", "beta"])
    assert set(got) == {0, 2}
    assert got[0] > got[2] > 0
    assert tfidf(docs, ["zzz"]) == {}

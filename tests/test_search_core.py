"""The search core (services/searchcore) and the callers that moved behind it."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

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

    fused = rrf([["a", "b", "c"], ["b", "a"]])
    assert fused["a"] == fused["b"] > fused["c"]
    assert sorted(fused, key=lambda i: (-fused[i], i)) == ["a", "b", "c"]
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


def test_search_sources_are_registered_and_cover_every_kind_in_order():
    from ddflow.services import search as S
    from ddflow.services.searchcore import registered

    names = [src.name for src in registered() if src.name in {"records", "sessions", "log"}]
    assert names == ["records", "sessions", "log"]
    covered = {k for src in registered() for k in src.kinds}
    assert set(S.SOURCES) <= covered
    assert {k for src in registered() if src.name in names for k in src.kinds} == set(S.SOURCES)


def _rule(repo, rid="r-style", content="small functions"):
    run_cli(repo, "init")
    code, _o, err = run_cli(
        repo, "rule", "add", "--id", rid, "--title", "Keep it short", "--content", content, "--new"
    )
    assert code == 0, err


def test_rule_search_regex_refuses_what_search_regex_refuses(repo):
    """B1778d8ab14: `rule search --regex` handed the raw pattern to `re`, so a catastrophic
    pattern ran unguarded and an uncompilable one read as 'No rules found'."""
    _rule(repo)
    for pattern in ("(a+)+b", r"(a)\1", "("):
        code, _out, err = run_cli(repo, "rule", "search", "--regex", pattern)
        assert code == 3, (pattern, code, err)
        assert "regex" in err, (pattern, err)
    code, out, _ = run_cli(repo, "rule", "search", "--regex", "small|short")
    assert code == 0 and "r-style" in out


@pytest.mark.timeout(60)
def test_rule_search_regex_cannot_hang_on_a_catastrophic_pattern(repo):
    _rule(repo, content="a" * 3000 + "!")
    import time

    t = time.monotonic()
    code, _out, _err = run_cli(repo, "rule", "search", "--regex", "(a+)+$")
    assert code == 3 and time.monotonic() - t < 20


def _lessons(log):
    log.append("lesson.recorded", "L1", {"title": "recovering from errors", "rule": "keep going"})
    log.append("lesson.recorded", "L2", {"title": "worktree per claim", "rule": "always claim"})
    log.append(
        "lesson.recorded",
        "L3",
        {"title": "claim the worktree before editing", "rule": "claim claim claim worktree"},
    )
    log.append("lesson.recorded", "L4", {"title": "unrelated", "rule": "nothing to see"})


def test_the_like_fallback_ranks_best_first_and_honours_the_limit(repo, log):
    """The fallback used to return the first rows a substring LIKE met; it now ranks with
    the shared BM25 over stemmed words, as FTS5 does."""
    from ddflow.config import Config
    from ddflow.infra.store import Store

    _lessons(log)
    cfg = Config.load()
    cfg.lessons.search_backend = "like"
    st = Store(repo, cfg)
    st.rebuild(log)
    got = [r["id"] for r in st.search("lessons", "claim worktree", 5)]
    assert got[0] == "L3" and set(got) == {"L2", "L3"}
    assert [r["id"] for r in st.search("lessons", "claim worktree", 1)] == ["L3"]
    assert st.search("lessons", "x", 5) == []  # too short to be a term, as for FTS5
    # a stop word is a word, as it is for FTS5: L3's title has "the"
    assert [r["id"] for r in st.search("lessons", "the", 5)] == ["L3"]
    assert st.search("lessons", "recover errors", 5)[0]["id"] == "L1"  # recovering~recover
    assert st.search("lessons", "zzzqqq", 5) == []


def test_both_backends_put_the_same_lesson_first(repo, log):
    from ddflow.config import Config
    from ddflow.infra.store import Store, _has_fts5

    if not _has_fts5():
        pytest.skip("this SQLite has no FTS5: only the like fallback exists here")

    _lessons(log)
    first = {}
    for backend in ("fts5", "like"):
        cfg = Config.load()
        cfg.lessons.search_backend = backend
        st = Store(repo, cfg)
        st.rebuild(log)
        first[backend] = st.search("lessons", "claim worktree", 5)[0]["id"]
    assert first["fts5"] == first["like"] == "L3"


def test_rule_search_regex_stays_case_insensitive(repo):
    _rule(repo)
    code, out, _ = run_cli(repo, "rule", "search", "--regex", "SMALL")
    assert code == 0 and "r-style" in out


def test_the_like_fallback_returns_only_rows_that_share_a_term(repo, log):
    from ddflow.config import Config
    from ddflow.infra.store import Store

    _lessons(log)
    cfg = Config.load()
    cfg.lessons.search_backend = "like"
    st = Store(repo, cfg)
    st.rebuild(log)
    assert [r["id"] for r in st.search("lessons", "unrelated", 20)] == ["L4"]

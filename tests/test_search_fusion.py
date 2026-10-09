"""Trigram + BM25 fusion in the store (B-uni-search-core.5-fusion).

The store ranks twice and fuses by reciprocal rank: BM25 over the porter words, and a trigram
BM25 over substrings. Where SQLite has FTS5 both come from its indexes; where it has none, or
no trigram tokenizer, the same two rankings are computed in Python from the same words, so a
query finds the same rows on every machine.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.config import Config
from ddflow.core import fuzzy, rank
from ddflow.infra import store as S
from ddflow.infra.store import Store, _has_fts5


def _lessons(log):
    log.append("lesson.recorded", "L1", {"title": "database migration order", "rule": "run first"})
    log.append("lesson.recorded", "L2", {"title": "claim the worktree", "rule": "always claim"})
    log.append("lesson.recorded", "L3", {"title": "unrelated", "rule": "nothing to see"})
    log.append("lesson.recorded", "L4", {"title": "claim claim", "rule": "worktree worktree claim"})


def _store(repo, backend="fts5", *, trigram=True, monkeypatch=None):
    cfg = Config.load()
    cfg.lessons.search_backend = backend
    if monkeypatch is not None and not trigram:
        monkeypatch.setattr(S, "_has_trigram", lambda: False)
    return Store(repo, cfg)


# -- the rankers (pure) ---------------------------------------------------------------


def test_substring_bm25_matches_what_the_trigram_index_matches():
    texts = ["Database MIGRATION order", "nothing here", "migrate, migrate, migrate"]
    got = rank.substring_bm25(texts, ["gration"])
    assert set(got) == {0}  # "migrate" does not contain "gration"
    assert set(rank.substring_bm25(texts, ["MIGRAT"])) == {0, 2}  # case-insensitive
    assert rank.substring_bm25(texts, ["mi"]) == {}  # under three characters matches nothing
    assert rank.substring_bm25(["is it up"], ["is", "it", "up"]) == {}
    assert rank.substring_bm25([], ["abc"]) == {} and rank.substring_bm25(texts, []) == {}
    two = rank.substring_bm25(texts, ["migrat"])
    assert two[2] > two[0]  # more occurrences rank higher at similar length


def test_fuse_orders_by_reciprocal_rank_with_id_ties_and_a_limit():
    assert rank.fuse([["a", "b", "c"], ["c", "b", "a"]], 3) == ["a", "c", "b"]
    assert rank.fuse([["x"], ["y"]], 5) == ["x", "y"]  # equal scores: id order
    assert rank.fuse([["a", "b", "c"]], 2) == ["a", "b"]
    assert rank.fuse([], 3) == []


def test_rerank_lifts_the_closest_text_and_keeps_order_among_equals(monkeypatch):
    monkeypatch.setattr(fuzzy, "_fuzz", None)  # the stdlib path, whatever is installed
    cands = [("a", "unrelated words"), ("b", "claim the worktree"), ("c", "unrelated words")]
    assert rank.rerank("claim the worktree", cands, 3) == ["b", "a", "c"]
    assert rank.rerank("claim the worktree", cands, 1) == ["b"]
    assert fuzzy.ratio("same", "same") == 1.0


def test_rerank_uses_rapidfuzz_when_the_extra_is_installed(monkeypatch):
    class Fake:
        @staticmethod
        def token_set_ratio(a, b):
            return 100.0 if b == "best" else 10.0

    monkeypatch.setattr(fuzzy, "_fuzz", Fake)
    assert fuzzy.ratio("q", "best") == 1.0 and fuzzy.ratio("q", "x") == 0.1
    assert fuzzy.available()
    assert rank.rerank("q", [("a", "x"), ("b", "best")], 2) == ["b", "a"]


# -- the store -------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["fts-trigram", "fts-no-trigram", "like"])
def test_a_substring_query_finds_the_same_lesson_on_every_machine(repo, log, monkeypatch, mode):
    """`gration` is no word and no porter stem of one: only a substring ranker finds
    `migration`. It must on a machine with the trigram index, with FTS5 alone and with neither."""
    if mode != "like" and not _has_fts5():
        pytest.skip("this SQLite has no FTS5")
    if mode == "fts-trigram" and not S._has_trigram():
        pytest.skip("this SQLite has no trigram tokenizer")
    _lessons(log)
    st = _store(repo, "like" if mode == "like" else "fts5", trigram=mode != "fts-no-trigram",
                monkeypatch=monkeypatch)  # fmt: skip
    st.rebuild(log)
    assert st.trigram is (mode == "fts-trigram")

    assert [r["id"] for r in st.search("lessons", "gration", 5)] == ["L1"]
    assert st.search("lessons", "zzzqqq", 5) == []
    assert st.search("lessons", "x", 5) == []


@pytest.mark.parametrize("mode", ["fts-trigram", "like"])
def test_the_word_query_keeps_its_order_and_fusion_does_not_drop_a_word_hit(
    repo, log, monkeypatch, mode
):
    if mode == "fts-trigram" and not (_has_fts5() and S._has_trigram()):
        pytest.skip("this SQLite has no FTS5 trigram")
    _lessons(log)
    st = _store(repo, "like" if mode == "like" else "fts5", monkeypatch=monkeypatch)
    st.rebuild(log)

    got = [r["id"] for r in st.search("lessons", "claim worktree", 5)]
    assert got[0] == "L4" and set(got) == {"L2", "L4"}
    assert [r["id"] for r in st.search("lessons", "claim worktree", 1)] == ["L4"]


def test_fts_and_the_python_rankers_agree_on_the_candidates(repo, log, monkeypatch):
    if not (_has_fts5() and S._has_trigram()):
        pytest.skip("this SQLite has no FTS5 trigram")
    _lessons(log)
    ids = {}
    for mode in ("fts-trigram", "like"):
        st = _store(repo, "like" if mode == "like" else "fts5")
        st.rebuild(log)
        ids[mode] = {r["id"] for r in st.search("lessons", "claim gration", 10)}
    assert ids["fts-trigram"] == ids["like"] == {"L1", "L2", "L4"}


@pytest.mark.parametrize("backend", ["fts5", "like"])
def test_prompts_fuse_on_their_session_ids_on_both_paths(repo, log, backend):
    if backend == "fts5" and not _has_fts5():
        pytest.skip("this SQLite has no FTS5")
    log.append("session.started", "S1", {"model": "m", "tool": "t"})
    log.append("session.prompt", "S1", {"text": "please migrate the database", "seq": 1})
    log.append("session.prompt", "S1", {"text": "unrelated chatter", "seq": 2})
    st = _store(repo, backend)
    st.rebuild(log)
    got = st.search("prompts", "igrat", 5)  # a substring of "migrate", no word of it
    assert [r["text"] for r in got] == ["please migrate the database"]
    assert got[0]["id"] == "S1#p0"  # the same id on both paths


@pytest.mark.parametrize("backend", ["fts5", "like"])
def test_a_query_with_no_usable_term_finds_nothing_on_every_machine(repo, log, backend):
    """The like fallback, rewritten around fusion, returned the first rows of the table
    for `""`/`"###"`/`"to be"` while FTS5 returned nothing."""
    if backend == "fts5" and not _has_fts5():
        pytest.skip("this SQLite has no FTS5")
    _lessons(log)
    st = _store(repo, backend)
    st.rebuild(log)
    for q in ("", "###", "x", "a b"):
        assert st.search("lessons", q, 5) == [], q


def test_overlapping_occurrences_count_as_a_trigram_index_counts_them():
    assert rank._occurrences("ababa", "aba") == 2
    assert rank._occurrences("abc", "zzz") == 0
    s = rank.substring_bm25(["ababa", "abaxx"], ["aba"])
    assert s[0] > s[1]


def test_ratio_is_case_insensitive_with_and_without_the_extra(monkeypatch):
    monkeypatch.setattr(fuzzy, "_fuzz", None)
    assert fuzzy.ratio("DNS", "dns") == 1.0

    class CaseSensitive:  # rapidfuzz's default: no processor, so case matters
        @staticmethod
        def token_set_ratio(a, b):
            return 100.0 if a == b else 0.0

    monkeypatch.setattr(fuzzy, "_fuzz", CaseSensitive)
    assert fuzzy.ratio("DNS", "dns") == 1.0


@pytest.mark.parametrize("backend", ["fts5", "like"])
def test_a_differently_cased_row_is_found_on_every_machine(repo, log, backend):
    if backend == "fts5" and not _has_fts5():
        pytest.skip("this SQLite has no FTS5")
    log.append("memory.recorded", "M1", {"text": "Quux release notes", "tags": []})
    st = _store(repo, backend)
    st.rebuild(log)
    assert [r["id"] for r in st.search("memories", "quu", 5)] == ["M1"]
    assert [r["id"] for r in st.search("memories", "QUUX", 5)] == ["M1"]


def test_the_optional_rerank_reorders_the_pool_not_just_the_page(repo, log):
    _lessons(log)
    st = _store(repo, "like")
    st.rebuild(log)
    plain = st.search("lessons", "claim worktree", 5)
    fuzzy_all = st.search("lessons", "claim worktree", 5, rerank_by_likeness=True)
    assert {r["id"] for r in plain} == {r["id"] for r in fuzzy_all}
    # a page of one is the closest of the whole pool, whatever the fused order says
    one = st.search("lessons", "claim worktree", 1, rerank_by_likeness=True)
    assert [r["id"] for r in one] == [fuzzy_all[0]["id"]]


@pytest.mark.parametrize("backend", ["fts5", "like"])
def test_a_late_short_term_is_ranked_on_every_machine(repo, log, backend):
    """Both word rankers take the same number of terms, so a short word past the eighth
    is found with or without FTS5."""
    if backend == "fts5" and not _has_fts5():
        pytest.skip("this SQLite has no FTS5")
    log.append("lesson.recorded", "L1", {"title": "zz ab", "rule": "r"})
    st = _store(repo, backend)
    st.rebuild(log)
    assert [r["id"] for r in st.search("lessons", "xx yy ww vv uu tt ss rr ab", 5)] == ["L1"]


def test_an_index_built_without_trigram_is_rebuilt_where_there_is_one(repo, log, monkeypatch):
    if not (_has_fts5() and S._has_trigram()):
        pytest.skip("this SQLite has no FTS5 trigram")
    _lessons(log)
    old = _store(repo, "fts5", trigram=False, monkeypatch=monkeypatch)
    old.rebuild(log)
    assert not old.stale(log)
    monkeypatch.undo()
    new = _store(repo, "fts5")
    assert new.trigram and new.stale(log)
    new.rebuild(log)
    assert not new.stale(log)
    assert {r["id"] for r in new.search("lessons", "claim gration", 10)} == {"L1", "L2", "L4"}

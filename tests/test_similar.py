"""The similarity engine (B-similar-engine): tokenizer, exact TF-IDF matchers, the
index.db projection and the [dedupe] policy. The accuracy bar itself is
tests/test_dedupe_eval.py; this file pins the parts that bar cannot see."""

from __future__ import annotations

import dataclasses
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ddflow.config import Config
from ddflow.core import textsim
from ddflow.infra import store as store_mod
from ddflow.infra.store import Store
from ddflow.services import similar
from tests.test_dedupe_eval import CORPUS


@pytest.fixture(autouse=True)
def _default_policy(monkeypatch):
    """The policy tests below read the DEFAULT [dedupe] ("ask"); conftest turns the add-time
    check off for the rest of the suite."""
    monkeypatch.delenv("DDFLOW_DEDUPE_ON_MATCH", raising=False)


# ----------------------------------------------------------------------------- tokenizer


def test_identifiers_split_into_their_words():
    toks = textsim.tokens(
        "adopt_existing in Store.rebuild", "see ddflow/infra/store.py and inferWorktree"
    )
    for w in ("adopt", "exist", "store", "rebuild", "ddflow", "infra", "py", "infer", "worktree"):
        assert w in toks, (w, toks)


def test_long_flags_are_kept_whole_as_well():
    toks = textsim.tokens("claim --no-worktree refuses", "")
    assert "--no-worktree" in toks and "worktree" in toks
    # a path segment or a word joined by dashes is not a flag
    assert not [t for t in textsim.tokens("x a/--b c--d", "") if t.startswith("--")]


def test_ids_shas_and_opaque_runs_are_masked_but_hex_words_are_not():
    toks = textsim.tokens(
        "B5c32cbb5c1 Bdeadbeefaa 9136eedf",
        "tree bridge-cse_015NbhRCT5XtidHYRvkjBCLK agent-a92981182770b640f defaced sha256",
    )
    for gone in ("nbh", "rct5", "xtid", "bclk", "b5c32cbb5c1", "bdeadbeefaa", "9136eedf"):
        assert gone not in toks, gone
    assert "defac" in toks and "sha256" in toks and "bridge" in toks


def test_import_stubs_and_stop_words_carry_nothing():
    assert textsim.tokens("", "Imported from docs/BACKLOG.md:102. The and of") == []


def test_the_stemmer_joins_inflections():
    assert {textsim.stem(w) for w in ("claims", "claimed", "claiming")} == {"claim"}
    assert textsim.stem("entries") == "entry" and textsim.stem("bugs") == "bugs"


def test_digest_ignores_case_and_whitespace_only():
    assert textsim.digest("Fix  it", "Now\n") == textsim.digest("fix it", "now")
    assert textsim.digest("fix it", "now") != textsim.digest("fix it", "later")


# ------------------------------------------------------------------------------ matcher

RECS = [
    {"id": "B1", "kind": "bug", "title": "", "body": "claim adopts the parent worktree"},
    {"id": "T1", "kind": "task", "title": "Claim creates its own worktree", "body": ""},
    {"id": "T2", "kind": "task", "title": "Render the board as html", "body": ""},
    {"id": "L1", "kind": "lesson", "title": "Leases expire", "body": "renew every five minutes"},
]


def test_query_is_best_first_bounded_and_cross_kind():
    hits = similar.build(RECS).query({"kind": "bug", "title": "", "body": "claim worktree"})
    ids = [h[0] for h in hits]
    assert set(ids) == {"B1", "T1"}, "records sharing no term score 0 and are left out"
    assert all(0.0 < s <= 1.0 for _, s in hits)
    assert [s for _, s in hits] == sorted((s for _, s in hits), reverse=True)


def test_identical_text_scores_one_and_limit_and_kinds_filter():
    idx = similar.build(RECS)
    hits = idx.query(RECS[1])
    assert hits[0] == ("T1", 1.0)
    assert idx.query(RECS[1], limit=1) == [("T1", 1.0)]
    assert [i for i, _ in idx.query(RECS[1], kinds=["bug"])] == ["B1"]
    assert idx.query({"kind": "task", "title": "the and of", "body": ""}) == []
    assert similar.build([]).query(RECS[0]) == []


def test_the_index_is_exact():
    """Every record sharing a term is scored, with the cosine a brute-force pair-by-pair
    computation gives: nothing is shortlisted away."""
    recs = [{k: r[k] for k in ("id", "kind", "title", "body")} for r in CORPUS[:200]]
    idx = similar.build(recs)
    toks = [similar.record_tokens(r) for r in recs]
    df, _ = textsim.invert(toks)
    vecs = [textsim.vector(t, df, len(recs)) for t in toks]
    for qi in range(0, 200, 17):
        got = dict(idx.query(recs[qi]))
        for j, v in enumerate(vecs):
            want = sum(x * v.get(t, 0.0) for t, x in vecs[qi].items())
            assert got.get(recs[j]["id"], 0.0) == pytest.approx(min(1.0, want), abs=1e-9)


# ------------------------------------------------------------------------------- store


def _project(path: Path, records: list[dict]) -> None:
    con = sqlite3.connect(path)
    Store(path.parent).init(con)
    store_mod._insert_similar(con, records)
    con.commit()
    con.close()


def test_store_and_memory_agree_exactly_on_the_fixture(tmp_path):
    recs = [{**{k: r[k] for k in ("id", "kind", "title", "body")}, "item": ""} for r in CORPUS]
    db = tmp_path / "index.db"
    _project(db, recs)
    mem = similar.build(recs)
    with similar.StoreIndex(db) as disk:
        for r in recs:
            assert disk.query(r) == mem.query(r), r["id"]
        assert disk.ids() == mem.ids() and disk.doc("BL-structural") == mem.doc("BL-structural")


def test_results_do_not_depend_on_fts5(repo, log):
    """The engine uses no FTS5, so a store without it answers identically."""
    log.append("task.added", "T1", {"title": "claim adopts the parent's worktree", "body": "x"})
    log.append("bug.found", "B1", {"summary": "subagent claim binds the parent worktree"})
    log.append("lesson.recorded", "L1", {"title": "worktree per claim", "rule": "always"})
    q = {"kind": "bug", "title": "", "body": "claim binds parent worktree"}
    answers = []
    for backend in ("fts5", "like"):
        cfg = Config.load()
        cfg.lessons.search_backend = backend
        st = Store(repo, cfg)
        st.rebuild(log)
        with similar.open_store(st) as idx:
            answers.append(idx.query(q))
    assert answers[0] == answers[1] and {i for i, _ in answers[0]} == {"T1", "B1", "L1"}


def test_the_projection_covers_every_kind_and_leaves_removed_out(repo, log, cfg):
    log.append("phase.added", "P1", {"title": "phase"})
    log.append("task.added", "T1", {"title": "kept", "parent": "P1"})
    log.append("task.added", "T2", {"title": "removed"})
    log.append("task.removed", "T2", {"reason": "dup"})
    log.append("bug.found", "B1", {"summary": "bug", "item": "T1"})
    log.append("lesson.recorded", "L1", {"title": "lesson", "rule": "r"})
    log.append("research.recorded", "R1", {"question": "q", "claim": "c"})
    log.append("memory.recorded", "M1", {"text": "a fact"})
    state = Store(repo, cfg).rebuild(log)
    recs = {r["id"]: r for r in store_mod.similar_records(state)}
    assert "T2" not in recs
    assert {recs[i]["kind"] for i in recs} >= {"phase", "task", "bug", "lesson", "research"}
    assert recs["B1"]["item"] == "T1" and recs["T1"]["item"] == "P1"


def test_a_tokenizer_change_makes_the_index_stale(repo, log, cfg, monkeypatch):
    st = Store(repo, cfg)
    st.rebuild(log)
    assert not st.stale(log)
    monkeypatch.setattr(textsim, "VERSION", textsim.VERSION + 1)
    assert st.stale(log)
    with pytest.raises(LookupError):
        similar.open_store(st)


def test_a_missing_index_is_refused_not_answered(repo, cfg):
    with pytest.raises(LookupError):
        similar.open_store(Store(repo, cfg))


# ------------------------------------------------------------------------------ policy

LONG = "subagent claim through the shared connection binds the parent session worktree"


def _cfg(**kw) -> Config:
    cfg = Config.load()
    cfg.dedupe = dataclasses.replace(cfg.dedupe, **{"on_match": "ask", **kw})
    return cfg


def _idx():
    return similar.build(
        [
            {"id": "B1", "kind": "bug", "title": "", "body": LONG, "item": "T9"},
            {"id": "T1", "kind": "task", "title": "fix the claim binding", "body": LONG},
            {"id": "T2", "kind": "task", "title": "board html", "body": "render it"},
            {"id": "T3", "kind": "task", "title": "unrelated", "body": "leases expire"},
        ]
    )


def test_a_close_long_record_asks_and_lists_cross_kind():
    a = similar.assess(_idx(), {"kind": "bug", "title": "", "body": LONG + " again"}, _cfg())
    assert a.action == "ask" and a.words >= 8
    assert [c.id for c in a.candidates][:2] == ["B1", "T1"]
    assert all(c.score >= 0.35 for c in a.candidates)


def test_identical_text_is_flagged():
    a = similar.assess(_idx(), {"kind": "bug", "title": "", "body": LONG.upper()}, _cfg())
    assert a.identical is not None and a.identical.id == "B1" and a.action == "ask"


def test_a_short_record_is_shown_not_asked():
    a = similar.assess(_idx(), {"kind": "task", "title": "board html", "body": ""}, _cfg())
    assert a.action == "show" and a.candidates[0].id == "T2" and a.words < 8


def test_naming_an_existing_id_asks_whatever_the_score():
    a = similar.assess(_idx(), {"kind": "task", "title": "follow-up to T3", "body": "x"}, _cfg())
    assert a.action == "ask" and any(c.id == "T3" and "named" in c.flags for c in a.candidates)


def test_a_plain_word_id_is_not_named_by_using_the_word():
    idx = similar.build([{"id": "cleanup", "kind": "task", "title": "tidy", "body": "x"}])
    a = similar.assess(idx, {"kind": "task", "title": "cleanup the board", "body": ""}, _cfg())
    assert a.action == "none"
    dotted = similar.build([{"id": "34.8g", "kind": "task", "title": "tidy", "body": "x"}])
    a = similar.assess(dotted, {"kind": "task", "title": "after 34.8g lands", "body": ""}, _cfg())
    assert a.action == "ask" and a.candidates[0].flags == ("named",)


def test_same_item_is_flagged_and_own_id_never_returned():
    rec = {"id": "B1", "kind": "bug", "title": "", "body": LONG, "item": "T9"}
    a = similar.assess(_idx(), {**rec, "id": "B2"}, _cfg())
    assert any(c.id == "B1" and "same_item" in c.flags for c in a.candidates)
    assert all(c.id != "B1" for c in similar.assess(_idx(), rec, _cfg()).candidates)


def test_the_knobs_steer_the_policy():
    q = {"kind": "bug", "title": "", "body": LONG + " reported today"}
    assert similar.assess(_idx(), q, _cfg(on_match="warn")).action == "warn"
    assert similar.assess(_idx(), q, _cfg(on_match="off")).action == "off"
    assert similar.assess(_idx(), q, _cfg(kinds=["task"])).action == "off"
    only_bugs = similar.assess(_idx(), q, _cfg(kinds=["bug"]))
    assert {c.kind for c in only_bugs.candidates} == {"bug"}
    assert len(similar.assess(_idx(), q, _cfg(max_candidates=1)).candidates) == 1
    assert similar.assess(_idx(), q, _cfg(ask_threshold=1.0)).action == "show"
    assert similar.assess(_idx(), q, _cfg(min_words=50)).action == "show"
    assert similar.assess(_idx(), q, _cfg(show_floor=1.0)).action == "none"


# ------------------------------------------------------------------------------ config


def test_dedupe_defaults_are_the_decisions():
    d = Config().dedupe
    assert (d.on_match, d.show_floor, d.ask_threshold, d.max_candidates, d.min_words) == (
        "ask",
        0.35,
        0.55,
        3,
        8,
    )
    assert "session" not in d.kinds and {"bug", "task", "lesson"} <= set(d.kinds)


@pytest.mark.parametrize(
    "bad",
    [
        {"on_match": "maybe"},
        {"show_floor": 1.5},
        {"ask_threshold": -0.1},
        {"max_candidates": 0},
        {"min_words": -1},
        {"kinds": ["prompt"]},
        {"kinds": []},
    ],
)
def test_bad_dedupe_values_are_refused(bad):
    with pytest.raises(ValueError):
        Config.check({"dedupe": bad})


def test_an_unknown_dedupe_knob_from_a_newer_tree_is_skipped_not_fatal(tmp_path):
    (tmp_path / ".ddflow").mkdir()
    (tmp_path / ".ddflow" / "config.toml").write_text(
        "[dedupe]\nask_threshold = 0.6\nfuture_knob = 1\n", "utf-8"
    )
    cfg = Config.load(tmp_path)
    assert cfg.dedupe.ask_threshold == 0.6 and "dedupe.future_knob" in cfg.unknown_knobs


def test_no_hit_scores_zero(monkeypatch):
    """No returned hit scores 0.0 (critic finding). Real data cannot produce a cosine
    under 5e-10 -- the smoothed IDF is >= 1 -- so the cosine is forced here: the filter
    must see the rounded score, not the raw one."""
    idx = similar.build([{"id": "A", "kind": "bug", "title": "alpha beta", "body": ""}])
    monkeypatch.setattr(textsim, "cosine", lambda q, p: {0: 1e-12})
    assert idx.query({"kind": "bug", "title": "alpha", "body": ""}, limit=None) == []


def test_rebuilding_twice_works_and_replaces_the_projection(repo, log, cfg):
    """rubber-duck finding, refuted: rebuild writes a fresh database and swaps it in, so
    the similarity tables are never inserted into twice."""
    log.append("task.added", "T1", {"title": "one thing", "body": "about claims"})
    st = Store(repo, cfg)
    st.rebuild(log)
    log.append("task.added", "T2", {"title": "another thing", "body": "about claims"})
    st.rebuild(log)
    with similar.open_store(st) as idx:
        assert idx.ids() == {"T1", "T2"}


def test_a_store_index_outliving_a_rebuild_answers_consistently_from_its_snapshot(repo, log, cfg):
    """critic finding: the instance keeps the file it opened, so a rebuild cannot give it
    new postings against old records (no IndexError, no wrong ids)."""
    log.append("task.added", "T1", {"title": "claims bind worktrees", "body": "x"})
    st = Store(repo, cfg)
    st.rebuild(log)
    with similar.open_store(st) as old:
        before = old.query({"kind": "task", "title": "claims bind worktrees", "body": ""})
        log.append("task.added", "T2", {"title": "claims bind worktrees too", "body": "x"})
        st.rebuild(log)
        assert old.query({"kind": "task", "title": "claims bind worktrees", "body": ""}) == before
    with similar.open_store(st) as new:
        assert {i for i, _ in new.query({"kind": "task", "title": "claims", "body": ""})} == {
            "T1",
            "T2",
        }


def test_store_and_memory_agree_past_fifty_candidates(tmp_path):
    """rubber-duck finding, refuted: nothing is shortlisted (no FTS5 top-50), so 300
    candidates sharing a term come back from the store exactly as from memory."""
    recs = [
        {
            "id": f"R{i}",
            "kind": "bug",
            "title": "",
            "body": f"common word{i % 7} uniq{i}x",
            "item": "",
        }
        for i in range(300)
    ]
    db = tmp_path / "index.db"
    _project(db, recs)
    q = {"kind": "bug", "title": "common word3", "body": ""}
    with similar.StoreIndex(db) as disk:
        got = disk.query(q)
    assert len(got) == 300 and got == similar.build(recs).query(q)


def test_concurrent_rebuilds_neither_traceback_nor_leave_a_broken_index(repo, log, cfg):
    """Bcdfb199cc0: parallel rebuilds shared ONE temp path and no lock, so one unlinked or
    published the other's half-built file (FileNotFoundError, `no such table: meta`)."""
    import threading

    for i in range(40):
        log.append("task.added", f"T{i}", {"title": f"thing {i}", "body": "about claims"})
    errors: list[BaseException] = []
    barrier = threading.Barrier(6)

    def run() -> None:
        try:
            barrier.wait()
            for _ in range(3):
                Store(repo, cfg).rebuild(log)
        except BaseException as exc:  # the assertion is that none happen
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    st = Store(repo, cfg)
    with similar.open_store(st) as idx:
        assert len(idx.ids()) == 40
    leftovers = [p.name for p in st.path.parent.iterdir() if "rebuilding" in p.name]
    assert leftovers == []


def test_ensure_does_not_traceback_when_the_rebuild_cannot_run(repo, log, cfg, monkeypatch):
    """A read path answers from the log when the index cannot be rebuilt (a held rebuild
    lock past its timeout, an unwritable directory): the index is a cache."""
    log.append("task.added", "T1", {"title": "one thing", "body": "x"})
    st = Store(repo, cfg)

    def boom(*a, **k):
        raise TimeoutError("could not acquire the index lock")

    monkeypatch.setattr(st, "_rebuild_locked", boom)
    state = st.ensure(log)
    assert "T1" in state.items

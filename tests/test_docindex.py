"""The documentation index: scope, incremental refresh, deletion, rename, schema bump, the
no-FTS5 fallback, redaction before indexing, and the single writer."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import threading
from pathlib import Path

import pytest

from ddflow.config import Config
from ddflow.infra.docindex import INDEX_LAYOUT, DocIndex, DocIndexError, IndexMissing
from ddflow.services.docsearch import scope as SC
from ddflow.services.export.safe import redact_text

SECRET = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
GUIDE = "# Guide\n\nHow to install the widget.\n\n## Usage\n\nRun the widget daily.\n"


def _git(repo: Path, *a: str) -> None:
    subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "docs").mkdir()
    (r / "docs/guide.md").write_text(GUIDE)
    (r / "docs/other.md").write_text("# Other\n\nSomething about gardens.\n")
    (r / "README.md").write_text("# Readme\n\nstart here\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "x")
    return r


@pytest.fixture
def idx(tmp_path: Path) -> DocIndex:
    return DocIndex(tmp_path / "local" / "docs.db")


def _sync(idx: DocIndex, repo: Path, **kw) -> SC.Refresh:
    kw.setdefault("redact", lambda t: t)
    return SC.refresh(idx, repo, SC.Scope(), **kw)


def _commit(repo: Path) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "y")


# -- scope -----------------------------------------------------------------------------


def test_scope_is_tracked_files_matching_globs(repo):
    (repo / "docs/untracked.md").write_text("# no\n")
    (repo / "notes.txt").write_text("text\n")
    _git(repo, "add", "notes.txt")
    got = SC.resolve(repo, SC.Scope())
    assert got.files == ["README.md", "docs/guide.md", "docs/other.md"]


def test_scope_excludes_caps_binary_and_generated(repo):
    (repo / "big.md").write_bytes(b"x" * 500)
    (repo / "bin.md").write_bytes(b"ab\0cd")
    (repo / "gen.md").write_text("<!-- ddflow:generated doc=status -->\n# S\n")
    _commit(repo)
    got = SC.resolve(repo, SC.Scope(exclude=("docs/**",), max_bytes=400))
    assert got.files == ["README.md"]
    assert "docs/guide.md" not in got.files
    assert "big" in got.skipped["big.md"] or "cap" in got.skipped["big.md"]
    assert got.skipped["bin.md"] == "binary"
    assert got.skipped["gen.md"] == "generated view"
    with_gen = SC.resolve(repo, SC.Scope(include_generated=True))
    assert "gen.md" in with_gen.files


def test_scope_refuses_when_git_cannot_list(tmp_path):
    with pytest.raises(OSError, match="tracked files"):
        SC.resolve(tmp_path, SC.Scope())


def test_from_config_reads_the_docs_check_rule(repo):
    cfg = Config()
    s = SC.from_config(cfg)
    assert "**/*.md" in s.include and ".ddflow/**" in s.exclude


# -- refresh ---------------------------------------------------------------------------


def test_first_refresh_builds_and_searches(idx, repo):
    r = _sync(idx, repo)
    assert r.rebuilt and sorted(r.added) == ["README.md", "docs/guide.md", "docs/other.md"]
    res = idx.search("installing widgets")
    assert [h.path for h in res.hits] == ["docs/guide.md"]  # porter: installing -> install
    assert res.mode == "words" and res.hits[0].heading == "Guide"
    st = idx.stats()
    assert st.files == 3 and st.chunks >= 3 and st.fts5 and st.schema == INDEX_LAYOUT


def test_second_refresh_touches_nothing(idx, repo):
    _sync(idx, repo)
    r = _sync(idx, repo)
    assert (r.added, r.changed, r.removed, r.touched) == ([], [], [], []) and r.unchanged == 3


def test_incremental_update_touches_only_changed_rows(idx, repo):
    _sync(idx, repo)
    with sqlite3.connect(idx.path) as con:
        ids = dict(con.execute("SELECT path, group_concat(id) FROM chunks GROUP BY path"))
    (repo / "docs/guide.md").write_text(GUIDE + "\nA new section about sprockets.\n")
    r = _sync(idx, repo)
    assert r.changed == ["docs/guide.md"] and not r.added and r.unchanged == 2
    with sqlite3.connect(idx.path) as con:
        after = dict(con.execute("SELECT path, group_concat(id) FROM chunks GROUP BY path"))
    assert after["README.md"] == ids["README.md"] and after["docs/other.md"] == ids["docs/other.md"]
    assert after["docs/guide.md"] != ids["docs/guide.md"]
    assert idx.search("sprockets").hits[0].path == "docs/guide.md"


def test_same_content_new_mtime_is_not_rechunked(idx, repo):
    _sync(idx, repo)
    p = repo / "docs/other.md"
    os.utime(p, ns=(1, 1))
    r = _sync(idx, repo)
    assert r.touched == ["docs/other.md"] and not r.changed
    assert _sync(idx, repo).unchanged == 3


def test_deleted_and_untracked_files_leave_the_index(idx, repo):
    _sync(idx, repo)
    (repo / "docs/other.md").unlink()
    r = _sync(idx, repo)
    assert r.removed == ["docs/other.md"]
    assert idx.search("gardens").hits == ()
    _git(repo, "rm", "-q", "--cached", "README.md")
    assert _sync(idx, repo).removed == ["README.md"]
    assert set(idx.files()) == {"docs/guide.md"}


def test_rename_moves_the_rows(idx, repo):
    _sync(idx, repo)
    _git(repo, "mv", "docs/other.md", "docs/renamed.md")
    r = _sync(idx, repo)
    assert r.removed == ["docs/other.md"] and r.added == ["docs/renamed.md"]
    assert [h.path for h in idx.search("gardens").hits] == ["docs/renamed.md"]
    assert "docs/other.md#" not in " ".join(h.chunk_id for h in idx.search("gardens").hits)


def test_only_limits_the_work_to_a_batch(idx, repo):
    _sync(idx, repo)
    (repo / "docs/guide.md").write_text(GUIDE + "\nzebra\n")
    (repo / "docs/other.md").write_text("# Other\n\nllama\n")
    r = SC.refresh(idx, repo, SC.Scope(), redact=lambda t: t, only=["docs/guide.md"])
    assert r.changed == ["docs/guide.md"]
    assert idx.search("llama").hits == () and idx.search("zebra").hits


def test_a_non_utf8_file_is_skipped_and_said(idx, repo):
    (repo / "latin.md").write_bytes("caf\xe9\n".encode("latin-1"))
    _commit(repo)
    r = _sync(idx, repo)
    assert "UTF-8" in r.skipped["latin.md"] and "latin.md" not in idx.files()


# -- schema and capabilities -----------------------------------------------------------


def test_schema_bump_rebuilds_atomically(idx, repo):
    _sync(idx, repo)
    with sqlite3.connect(idx.path) as con:
        con.execute("UPDATE meta SET value='0' WHERE key='schema'")
    with pytest.raises(IndexMissing, match="another schema"):
        idx.stats()
    r = _sync(idx, repo)
    assert r.rebuilt and len(r.added) == 3
    assert idx.stats().schema == INDEX_LAYOUT
    assert not idx.path.with_name("docs.db.new").exists()


def test_a_corrupt_index_is_rebuilt(idx, repo):
    idx.path.parent.mkdir(parents=True)
    idx.path.write_bytes(b"not a database at all" * 50)
    assert _sync(idx, repo).rebuilt
    assert idx.search("widget").hits


def test_missing_index_says_how_to_build_it(idx):
    with pytest.raises(IndexMissing, match="has not been built"):
        idx.search("x")


def test_a_reader_is_not_blocked_by_the_writer(idx, repo):
    _sync(idx, repo)
    with idx.writer() as w:
        w.drop(["README.md"])
        assert idx.search("widget").hits  # the old snapshot, read while the write is open


def test_no_fts5_falls_back_to_like_and_says_so(tmp_path, repo):
    plain = DocIndex(tmp_path / "plain.db", fts5=False)
    _sync(plain, repo)
    res = plain.search("widget")
    assert res.mode == "like" and "FTS5" in res.note
    assert {h.path for h in res.hits} == {"docs/guide.md"}
    with sqlite3.connect(plain.path) as con:
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master")}
    assert "chunks_fts" not in names and "chunks_tri" not in names
    (repo / "docs/wild.md").write_text("# Wild\n\nsnake_case and a --> b\n")
    (repo / "docs/wild2.md").write_text("# Wild\n\nsnakexcase\n")
    _git(repo, "add", "-A")
    _sync(plain, repo)
    # `_` is a LIKE wildcard: unescaped, "snake_case" would also match docs/wild2.md
    assert [h.path for h in plain.search("snake_case").hits] == ["docs/wild.md"]
    assert len(plain.search("-->", mode="substring").hits) == 1
    (repo / "docs/ab.md").write_text("# Ab\n\nhello world\n")
    (repo / "docs/ba.md").write_text("# Ba\n\nworld hello\n")
    _git(repo, "add", "-A")
    _sync(plain, repo)
    assert [h.path for h in plain.search("hello world", mode="substring").hits] == ["docs/ab.md"]
    assert len(plain.search("hello world").hits) == 2  # words: each word, any order


def test_substring_of_punctuation_only(idx, repo):
    (repo / "docs/arrow.md").write_text("# Arrow\n\na --> b and c ... d\n")
    _git(repo, "add", "-A")
    _sync(idx, repo)
    for q in ("-->", "..."):
        res = idx.search(q, mode="substring")
        assert res.mode == "substring" and [h.path for h in res.hits] == ["docs/arrow.md"]
    assert idx.search("   ", mode="substring").hits == ()
    for q in ('"""', "***", "((("):  # FTS5 metacharacters are quoted, never a syntax error
        assert idx.search(q, mode="substring").hits == ()


def test_no_trigram_degrades_substring_search(tmp_path, repo):
    idx = DocIndex(tmp_path / "w.db", trigram=False)
    _sync(idx, repo)
    res = idx.search("idge", mode="substring")
    assert res.mode == "like" and "trigram" in res.note
    assert idx.search("wid", mode="words").mode == "words"


def test_substring_search_uses_trigrams(idx, repo):
    _sync(idx, repo)
    res = idx.search("idget", mode="substring")  # inside "widget": no word starts with it
    assert res.mode == "substring" and {h.path for h in res.hits} == {"docs/guide.md"}
    assert idx.search("idget", mode="words").hits == ()
    assert idx.search("ab", mode="substring").mode == "like"


def test_search_modes_are_checked(idx, repo):
    _sync(idx, repo)
    with pytest.raises(ValueError, match="mode is one of"):
        idx.search("x", mode="fuzzy")
    assert idx.search("!!!").hits == ()


# -- redaction -------------------------------------------------------------------------


def test_a_secret_is_not_findable(idx, repo):
    (repo / "docs/ops.md").write_text(
        f"# Ops\n\nthe token is {SECRET} keep it safe\n\n```\nkey {SECRET}\n```\n"
    )
    _commit(repo)
    _sync(idx, repo, redact=lambda t: redact_text(t, Config()).text)
    for mode in ("words", "substring", "like"):
        assert idx.search(SECRET, mode=mode).hits == ()
        assert idx.search("abcdefghijklmnopqrstuvwxyz0123456789", mode=mode).hits == ()
    assert idx.search("safe").hits  # the rest of the chunk is still searchable
    with sqlite3.connect(idx.path) as con:
        assert not con.execute(
            "SELECT 1 FROM chunks WHERE text LIKE ?", (f"%{SECRET}%",)
        ).fetchone()


def test_a_multi_line_secret_cannot_straddle_chunks(idx, repo):
    key = "-----BEGIN PRIVATE KEY-----\nAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n-----END PRIVATE KEY-----"
    (repo / "k.md").write_text(f"# K\n\n{key}\n")
    _commit(repo)
    _sync(idx, repo, redact=lambda t: t.replace(key, "[REDACTED]"))
    assert idx.search("AAAAAAAAAAAAAAAAAAAAAAAAAAAA", mode="like").hits == ()


# -- the single writer -----------------------------------------------------------------


def test_a_second_writer_waits_then_times_out(tmp_path, repo):
    idx = DocIndex(tmp_path / "l.db", lock_timeout_s=0.3)
    held, release = threading.Event(), threading.Event()

    def hold() -> None:
        with idx.writer():
            held.set()
            release.wait(5)

    t = threading.Thread(target=hold)
    t.start()
    held.wait(5)
    try:
        with pytest.raises(DocIndexError, match="another process"), idx.writer():
            pass
    finally:
        release.set()
        t.join()
    with idx.writer():  # free again
        pass


def test_a_failed_write_changes_nothing(idx, repo):
    _sync(idx, repo)
    before = idx.files()
    with pytest.raises(RuntimeError), idx.writer() as w:
        w.drop(["README.md"])
        raise RuntimeError("boom")
    assert idx.files() == before


def test_an_index_built_with_fts_opened_without_it_falls_back(tmp_path, repo):
    built = DocIndex(tmp_path / "x.db")
    _sync(built, repo)
    res = DocIndex(tmp_path / "x.db", fts5=False).search("widget")
    assert res.mode == "like" and "FTS5" in res.note and res.hits

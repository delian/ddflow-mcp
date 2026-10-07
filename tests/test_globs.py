"""core.globs: the one glob semantics (D-unify). Behaviour of the five matchers it replaced
is pinned with a table of the globs this repository really uses, and with properties."""

from __future__ import annotations

import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ddflow.core import globs
from ddflow.core.schedule import globs_overlap, is_shared, path_in_glob
from ddflow.services import docsync, importer, inventory

# (glob, path, pathspec, any_depth): what the pre-unification matchers answered
TABLE = [
    ("ddflow/**", "ddflow/core/x.py", True, True),
    ("ddflow/**", "ddflow/x.py", True, True),
    ("ddflow/**", "other/ddflow/x.py", False, False),
    ("**/*.md", "README.md", True, True),
    ("**/*.md", "docs/a/b.md", True, True),
    ("**/*.md", "a.mdx", False, False),
    ("*.md", "README.md", True, True),
    ("*.md", "docs/README.md", False, True),
    ("docs/*.md", "docs/a.md", True, True),
    ("docs/*.md", "docs/a/b.md", False, False),
    ("docs/**/x.md", "docs/x.md", True, True),
    ("docs/**/x.md", "docs/a/b/x.md", True, True),
    ("src/?.py", "src/a.py", True, True),
    ("src/?.py", "src/ab.py", False, False),
    ("a.md", "a.md.bak", False, False),
    ("a.md", "sub/a.md", False, True),
    ("/a.md", "a.md", True, True),
    ("a[0-9].md", "a5.md", True, True),
    ("a[!0-9].md", "a5.md", False, False),
    ("a[^0-9].md", "ax.md", True, True),
]


@pytest.mark.parametrize(("glob", "path", "pathspec", "any_depth"), TABLE)
def test_table_of_real_globs(glob, path, pathspec, any_depth):
    assert globs.match(path, glob) is pathspec
    assert globs.match(path, glob, bare_any_depth=True) is any_depth


@pytest.mark.parametrize(("glob", "path"), [(g, p) for g, p, ps, _ in TABLE if ps])
def test_doc_sync_agrees_with_pathspec_match(glob, path):
    assert docsync.is_doc(path, [glob]) is True
    assert docsync.glob_regex(glob).fullmatch(path)


def test_a_pattern_git_reads_and_python_cannot_matches_only_itself():
    assert globs.match("a[z-a]", "a[z-a]") is True
    assert globs.match("az", "a[z-a]") is False
    assert is_shared("x", ["a[z-a]"]) is False


@pytest.mark.parametrize(
    ("path", "glob", "inside"),
    [
        ("src/a.py", "src/", True),
        ("src/a.py", "src", True),
        ("src/a/b.py", "src/*.py", True),  # a claim's `*` crosses `/`, as written so far
        ("src/b.py", "src/**/*.py", True),
        ("a.md", "a.md.bak", False),
        ("sub/a.md", "a.md", False),  # a claim on a bare name is one file
    ],
)
def test_inside_is_the_claim_question(path, glob, inside):
    assert path_in_glob(path, glob) is inside
    assert globs.inside(path, glob) is inside


def test_overlap_is_symmetric_on_the_table():
    pats = [g for g, *_ in TABLE] + ["*", "ddflow/core/**", "ddflow/core/x.py"]
    for a in pats:
        for b in pats:
            assert globs_overlap(a, b) is globs_overlap(b, a)
    assert globs.overlap("ddflow/core/x.py", "ddflow/**")
    assert not globs.overlap("docs/a.md", "ddflow/**")
    assert globs.overlap("*", "anything")


_ALPHA = st.sampled_from(["a", "b", ".md", "*", "**", "?", "/", "docs", "x.py", "[ab]"])
_glob = st.lists(_ALPHA, min_size=1, max_size=5).map("".join)
_path = st.lists(st.sampled_from(["a", "b", "docs", "x.py", "a.md"]), min_size=1, max_size=4).map(
    "/".join
)


@given(_glob, _path)
def test_match_never_raises_and_is_equality_at_least(glob, path):
    assert isinstance(globs.match(path, glob), bool)
    assert globs.match(glob, glob) is True


@given(_glob, _path)
def test_any_depth_is_never_narrower_than_pathspec(glob, path):
    if globs.match(path, glob):
        assert globs.match(path, glob, bare_any_depth=True)


@given(_glob, _glob)
def test_overlap_is_reflexive_and_symmetric(a, b):
    assert globs.overlap(a, a)
    assert globs.overlap(a, b) is globs.overlap(b, a)


def test_one_translator_serves_every_caller():
    assert docsync.glob_regex("docs/**") is globs.regex("docs/**")
    assert re.compile(r"\Z").pattern in globs.regex("a").pattern


def test_lesson_globs_match_by_name_at_any_depth(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "m.py").write_text("x")
    (tmp_path / "t.py").write_text("x")
    got = inventory._candidates(tmp_path, ["*.py"])
    assert sorted(p.name for p in got) == ["m.py", "t.py"]


def test_importer_archive_globs_use_the_same_reading():
    assert importer.glob_match("docs/todo.md", "docs/todo.md")
    assert importer.glob_match("docs/old/a.md", "docs/**/a.md")
    assert not importer.glob_match("docs/old/a.md", "docs/*.md")


def test_a_doc_glob_with_a_class_matches_as_git_reads_it():
    """B0cf7a6ddea: docsync escaped `[`, so `docs/[ab].md` named no doc."""
    assert docsync.is_doc("docs/a.md", ["docs/[ab].md"])
    assert not docsync.is_doc("docs/c.md", ["docs/[ab].md"])


def test_a_bare_name_archive_glob_holds_a_nested_plan_file(repo):
    """B0cf7a6ddea: `archive_globs = ["todo.md"]` held docs/todo.md under fnmatch's reading of
    the name at any depth; the first unified reading anchored it and imported the work open."""
    from conftest import run_cli

    from ddflow.core.model import BLOCKED, fold
    from ddflow.infra.log import EventLog

    (repo / "docs").mkdir()
    (repo / "docs" / "todo.md").write_text("## Legacy\n\n- [ ] **159.A.1** - one\n")
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text('[importer]\narchive_globs = ["todo.md"]\n')
    code, _out, err = run_cli(repo, "import", "--apply")
    assert code == 0, err
    st = fold(EventLog(repo).read_all(), strict=False)
    assert st.items["159.A.1"].state == BLOCKED

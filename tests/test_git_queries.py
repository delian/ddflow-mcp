"""One meaning per git query (D-unify, B-uni-git-queries): the unified-diff reader."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ddflow.core import unidiff
from ddflow.infra import worktree as W


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.org")
    _git(tmp_path, "config", "user.name", "t")
    return tmp_path


def _touched(diff: str) -> set[str]:
    return {p for f in unidiff.files(diff) for p in f.paths}


def _diff(repo: Path) -> str:
    return W.capture_diff(repo, None, include_untracked=True)


def test_sections_name_exact_paths_for_every_change_kind(repo):
    (repo / "keep.txt").write_text("a\n")
    (repo / "gone.txt").write_text("b\n")
    (repo / "old name.txt").write_text("c\nd\ne\nf\ng\nh\n")
    (repo / "mode.sh").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    (repo / "keep.txt").write_text("a2\n")
    (repo / "gone.txt").unlink()
    _git(repo, "mv", "old name.txt", "new name.txt")
    (repo / "mode.sh").chmod(0o755)
    (repo / "added.txt").write_text("n\n")
    diff = _diff(repo)
    by_path = {f.path: f for f in unidiff.files(diff)}
    assert set(by_path) == {"keep.txt", "gone.txt", "new name.txt", "mode.sh", "added.txt"}
    assert by_path["gone.txt"].old == "gone.txt" and by_path["gone.txt"].new is None
    assert by_path["added.txt"].old is None
    assert by_path["new name.txt"].paths == ("old name.txt", "new name.txt")
    assert by_path["mode.sh"].paths == ("mode.sh",)


def test_a_name_with_a_space_is_not_found_in_a_longer_names_header(repo):
    """Mutant: matching a header by prefix. `foo` changed but missing from the diff must
    not be satisfied by the header of a changed `foo bar` (git leaves it unquoted)."""
    (repo / "foo").write_text("1\n")
    (repo / "foo bar").write_text("1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    (repo / "foo").write_text("2\n")
    (repo / "foo bar").write_text("2\n")
    full = _diff(repo)
    assert W.diff_covers_everything(repo, full) == (True, [])
    only_bar = "".join(s for s in unidiff.sections(full) if "foo bar" in s.split("\n")[0])
    ok, missing = W.diff_covers_everything(repo, only_bar)
    assert not ok and missing == ["foo"]


def test_a_quoted_name_is_read_back_as_the_files_own_name(repo):
    name = 'café "q".txt'
    (repo / name).write_text("1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    (repo / name).write_text("2\n")
    diff = _diff(repo)
    assert _touched(diff) == {name}
    assert W.diff_covers_everything(repo, diff) == (True, [])


def test_header_only_sections_fall_back_to_the_diff_line():
    mode = "diff --git a/x y.sh b/x y.sh\nold mode 100644\nnew mode 100755\n"
    assert [f.paths for f in unidiff.files(mode)] == [("x y.sh",)]
    assert unidiff.header_path("+++ /dev/null", "b/") is None
    assert unidiff.unquote('"a\\\\b"') == "a\\b" and unidiff.unquote("a\\b") == "a\\b"


def test_content_lines_that_look_like_headers_are_not_headers():
    diff = "diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1 +1 @@\n--- a/evil\n+++ b/evil\n"
    assert _touched(diff) == {"f"}


def test_a_binary_add_and_delete_say_which_side_is_missing():
    diff = (
        "diff --git a/add.bin b/add.bin\nnew file mode 100644\nindex 0000000..cedf4fb\n"
        "Binary files /dev/null and b/add.bin differ\n"
        "diff --git a/gone.bin b/gone.bin\ndeleted file mode 100644\nindex 0f49c4a..0000000\n"
        "Binary files a/gone.bin and /dev/null differ\n"
    )
    added, gone = unidiff.files(diff)
    assert (added.old, added.new) == (None, "add.bin")
    assert (gone.old, gone.new) == ("gone.bin", None)


def test_two_non_utf8_names_are_not_confused_when_the_diff_drops_one(repo):
    """Mutant: comparing names through U+FFFD alone. `a\\xe9` and `a\\x80` both become
    `a\\ufffd` there; the default C-quoted octal spelling keeps them apart."""
    import os

    one, two = os.fsdecode(b"a\xe9"), os.fsdecode(b"a\x80")
    for name in (one, two):
        (repo / name).write_text("1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    for name in (one, two):
        (repo / name).write_text("2\n")
    full = _diff(repo)
    assert W.diff_covers_everything(repo, full) == (True, [])
    only_one = "".join(s for s in unidiff.sections(full) if "\\351" in s.split("\n")[0])
    assert only_one
    assert W.diff_covers_everything(repo, only_one) == (False, [two])


def test_lossy_names_cover_only_as_many_changed_files_as_the_diff_shows(repo):
    """Under `core.quotepath=false` two non-UTF-8 names read the same in the diff text, so
    a diff that shows one section must not cover both changed files."""
    import os

    one, two = os.fsdecode(b"a\xe9"), os.fsdecode(b"a\x80")
    _git(repo, "config", "core.quotepath", "false")
    for name in (one, two):
        (repo / name).write_text("1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    for name in (one, two):
        (repo / name).write_text("2\n")
    full = _diff(repo)
    assert W.diff_covers_everything(repo, full) == (True, [])
    first = next(unidiff.sections(full))
    ok, missing = W.diff_covers_everything(repo, first)
    assert not ok and len(missing) == 1


def test_header_only_sections_with_two_different_names_split_at_the_boundary():
    """Mutant: the final `return None, pair`. A `--no-index` binary diff names two files."""
    plain = (
        "diff --git a/old.bin b/new.bin\nindex 0..1\nBinary files a/old.bin and b/new.bin differ\n"
    )
    one_quoted = 'diff --git a/old.txt "b/n\\303\\266.bin"\nBinary files differ\n'
    both = 'diff --git "a/\\303\\266 x" "b/\\303\\266 y"\nBinary files differ\n'
    assert [f.paths for f in unidiff.files(plain)] == [("old.bin", "new.bin")]
    assert [f.paths for f in unidiff.files(one_quoted)] == [("old.txt", "n\u00f6.bin")]
    assert [f.paths for f in unidiff.files(both)] == [("\u00f6 x", "\u00f6 y")]


# ---- changes.changed_paths / base_changed (B-uni-git-queries.3-changes) -------------------

from ddflow.services import changes as CH  # noqa: E402


@pytest.fixture
def forked(repo):
    """main with one commit, a branch `work` that forked from it and moved on, and main
    moved on too."""
    (repo / "base.txt").write_text("b\n")
    (repo / "old name.txt").write_text("c\nd\ne\nf\ng\nh\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "checkout", "-qb", "work")
    (repo / "committed.txt").write_text("1\n")
    _git(repo, "add", "committed.txt")
    _git(repo, "mv", "old name.txt", "café.txt")
    _git(repo, "commit", "-qm", "work")
    _git(repo, "checkout", "-q", "main")
    (repo / "on main.txt").write_text("m\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "main moves")
    _git(repo, "checkout", "-q", "work")
    return repo


def test_changed_paths_reads_every_kind_and_both_sides_of_a_rename(forked):
    repo = forked
    (repo / "base.txt").write_text("b2\n")  # worktree
    (repo / "staged.txt").write_text("s\n")
    _git(repo, "add", "staged.txt")
    (repo / "new file.txt").write_text("u\n")  # untracked
    got = CH.changed_paths(repo, "main")
    assert got == sorted(
        ["committed.txt", "café.txt", "old name.txt", "base.txt", "staged.txt", "new file.txt"]
    )  # nothing from main's own commit; exact (-z) names, a rename is both paths
    assert CH.changed_paths(repo, "main", include=("committed",)) == [
        "café.txt",
        "committed.txt",
        "old name.txt",
    ]
    assert CH.changed_paths(repo, include=("staged",)) == ["staged.txt"]
    assert CH.changed_paths(repo, include=("untracked",)) == ["new file.txt"]
    assert CH.changed_paths(repo, include=("tracked",)) == ["base.txt", "staged.txt"]


def test_changed_paths_renames_flag_and_filter(forked):
    repo = forked
    assert CH.changed_paths(repo, "main", include=("committed",), renames=True) == [
        "café.txt",
        "committed.txt",
    ]
    assert CH.changed_paths(
        repo, "main", include=("committed",), renames=True, diff_filter="ACMR"
    ) == ["café.txt", "committed.txt"]
    assert CH.changed_paths(repo, "main", include=("committed",), pathspec=("café.txt",)) == [
        "café.txt"
    ]


def test_changed_paths_without_fork_compares_the_two_commits_as_they_are(forked):
    got = CH.changed_paths(forked, "main", tip="work", include=("committed",), fork=False)
    assert "on main.txt" in got and "committed.txt" in got


def test_a_git_failure_is_unknown_never_unchanged(forked, tmp_path_factory):
    assert CH.changed_paths(forked, "no-such-ref") is None
    assert CH.base_changed(forked, "no-such-ref") is None
    outside = tmp_path_factory.mktemp("not-a-repo")
    assert CH.changed_paths(outside, "main") is None
    assert CH.base_changed(forked, "main") is True
    _git(forked, "checkout", "-q", "main")
    assert CH.base_changed(forked, "main") is False


def test_changed_paths_refuses_an_unknown_kind_or_a_committed_read_without_a_base(forked):
    with pytest.raises(ValueError):
        CH.changed_paths(forked, "main", include=("bogus",))
    with pytest.raises(ValueError):
        CH.changed_paths(forked, include=("committed",))


def test_capture_diff_raises_when_the_base_cannot_be_resolved(forked):
    """B-uni-git-queries.3: a failed merge-base fell back to the base and read as an empty
    diff, "nothing changed"."""
    with pytest.raises(RuntimeError):
        W.capture_diff(forked, "no-such-ref", include_untracked=False)
    assert "committed.txt" in W.capture_diff(forked, "main", include_untracked=False)


def test_changed_paths_with_nothing_to_read_is_refused_not_empty(forked):
    for kw in ({"tip": "work"}, {"include": ()}):
        with pytest.raises(ValueError):
            CH.changed_paths(forked, **kw)


def test_literal_pathspecs_apply_to_untracked_too(forked):
    (forked / "a1.py").write_text("x\n")
    (forked / "a[1].py").write_text("x\n")
    got = CH.changed_paths(forked, include=("untracked",), pathspec=("a[1].py",), literal=True)
    assert got == ["a[1].py"]


def test_capture_diff_with_no_common_ancestor_still_diffs(forked):
    """git's merge-base exit 1 is an answer (unrelated history), not a failure."""
    _git(forked, "checkout", "-q", "--orphan", "other")
    _git(forked, "rm", "-rqf", ".")
    (forked / "o.txt").write_text("o\n")
    _git(forked, "add", "o.txt")
    _git(forked, "commit", "-qm", "orphan")
    assert "o.txt" in W.capture_diff(forked, "main", include_untracked=False)


def test_diff_for_says_unavailable_when_git_cannot_read_the_tree(tmp_path_factory):
    from types import SimpleNamespace

    from ddflow.api import review as R

    cfg = SimpleNamespace(worktree=SimpleNamespace(base_ref=""))
    outside = tmp_path_factory.mktemp("not-a-repo")
    diff, how = R.diff_for(outside, cfg, SimpleNamespace(items={}), "")
    assert diff == "" and "could not read it" in how


def test_no_common_ancestor_is_unknown_for_changed_paths(forked):
    _git(forked, "checkout", "-q", "--orphan", "other")
    _git(forked, "rm", "-rqf", ".")
    (forked / "o.txt").write_text("o\n")
    _git(forked, "add", "o.txt")
    _git(forked, "commit", "-qm", "orphan")
    assert CH.changed_paths(forked, "main") is None  # base...HEAD has no merge base


def test_literal_files_refuses_an_exclude(forked):
    from ddflow.infra import git as G

    with pytest.raises(ValueError):
        G.files(forked, "all", exclude=(":(exclude)x",), literal=True)

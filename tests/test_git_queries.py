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
    assert unidiff.touched_paths(diff) == {name}
    assert W.diff_covers_everything(repo, diff) == (True, [])


def test_header_only_sections_fall_back_to_the_diff_line():
    mode = "diff --git a/x y.sh b/x y.sh\nold mode 100644\nnew mode 100755\n"
    assert [f.paths for f in unidiff.files(mode)] == [("x y.sh",)]
    assert unidiff.header_path("+++ /dev/null", "b/") is None
    assert unidiff.unquote('"a\\\\b"') == "a\\b" and unidiff.unquote("a\\b") == "a\\b"


def test_content_lines_that_look_like_headers_are_not_headers():
    diff = (
        "diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1 +1 @@\n"
        "--- a/evil\n+++ b/evil\n"
    )
    assert unidiff.touched_paths(diff) == {"f"}


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

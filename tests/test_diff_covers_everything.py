"""The review diff's completeness check reads git's paths exactly (B7ab10b58f2).

`diff_covers_everything` parsed `git status --porcelain` with ``ln[3:]`` after `W.git`
had stripped the output, so the first path lost its first character (hidden by a
substring test, total for a one-letter name), and a rename's `a -> b` line was reported
as a missing file. `capture_diff` listed untracked files without `-z`, so a non-ASCII
name came back C-quoted and `git add -N` never added it: the reviewer did not see it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.infra import worktree as W


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _commit(repo: Path, name: str, text: str = "one\n") -> None:
    (repo / name).write_text(text)
    _git(repo, "add", name)
    _git(repo, "commit", "-qm", f"add {name}")


def test_a_one_letter_path_missing_from_the_diff_is_reported(repo):
    _commit(repo, "x")
    (repo / "x").write_text("two\n")
    ok, missing = W.diff_covers_everything(repo, "")
    assert not ok and missing == ["x"], missing


def test_a_rename_is_covered_by_the_diff_that_shows_it(repo):
    _commit(repo, "old_name.py")
    _git(repo, "mv", "old_name.py", "new_name.py")
    diff = W.capture_diff(repo)
    ok, missing = W.diff_covers_everything(repo, diff)
    assert ok, missing


def test_a_non_ascii_untracked_file_reaches_the_review_diff(repo):
    _commit(repo, "a.py")
    (repo / "café.txt").write_text("new file\n")
    diff = W.capture_diff(repo)
    assert "new file" in diff, diff
    ok, missing = W.diff_covers_everything(repo, diff)
    assert ok, missing
    assert "café.txt" in W.untracked_files(repo)


def test_a_tree_git_cannot_read_is_not_complete(tmp_path):
    ok, missing = W.diff_covers_everything(tmp_path / "not-a-repo", "")
    assert not ok and missing, missing


@pytest.mark.parametrize("name", ['a"b.txt', "a\\b.txt", "tab\there.txt"])
def test_a_name_git_quotes_in_the_diff_is_still_found(repo, name):
    _commit(repo, name)
    (repo / name).write_text("two\n")
    diff = W.capture_diff(repo)
    ok, missing = W.diff_covers_everything(repo, diff)
    assert ok, (missing, diff)


def test_a_non_utf8_name_does_not_break_the_review_diff(repo):
    _commit(repo, "a.py")
    raw = os.fsdecode(b"caf\xe9.txt")  # Latin-1 bytes: not UTF-8
    (repo / raw).write_text("new file\n")
    diff = W.capture_diff(repo)
    assert "new file" in diff, diff
    ok, missing = W.diff_covers_everything(repo, diff)
    assert ok, missing


def test_non_utf8_content_does_not_break_the_review_diff(repo):
    _commit(repo, "latin.txt")
    (repo / "latin.txt").write_bytes(b"caf\xe9 content\n")
    diff = W.capture_diff(repo)
    assert "content" in diff, diff


def test_a_mixed_name_under_quotepath_false_is_found(repo):
    """`core.quotepath=false` escapes `"` but leaves the non-ASCII bytes raw."""
    _git(repo, "config", "core.quotepath", "false")
    _commit(repo, 'café"b.txt')
    (repo / 'café"b.txt').write_text("two\n")
    ok, missing = W.diff_covers_everything(repo, W.capture_diff(repo))
    assert ok, missing

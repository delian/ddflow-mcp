"""services.changes.ChangeDetector and changed_since (B-uni-local-worker.2-changes)."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from ddflow.services import changes as C


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _write(root: Path, rel: str, text: str) -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text)


class Counting:
    """A hasher that counts the reads."""

    def __init__(self) -> None:
        self.reads: list[str] = []

    def __call__(self, path: Path) -> str | None:
        self.reads.append(path.name)
        return C.file_hash(path)


def test_scan_hashes_matching_files_by_content(tmp_path):
    _write(tmp_path, "docs/a.md", "one")
    _write(tmp_path, "docs/sub/b.md", "two")
    _write(tmp_path, "src/c.py", "three")
    _write(tmp_path, ".git/config", "ignored")
    det = C.ChangeDetector(tmp_path, ("docs/**",))
    manifest = det.scan()
    assert sorted(manifest) == ["docs/a.md", "docs/sub/b.md"]
    assert manifest["docs/a.md"] != manifest["docs/sub/b.md"]
    assert set(C.ChangeDetector(tmp_path).scan()) == {"docs/a.md", "docs/sub/b.md", "src/c.py"}


def test_changes_report_added_modified_and_removed(tmp_path):
    _write(tmp_path, "a.md", "1")
    _write(tmp_path, "b.md", "1")
    det = C.ChangeDetector(tmp_path, ("*.md",))
    first = det.scan()
    _write(tmp_path, "a.md", "22")
    (tmp_path / "b.md").unlink()
    _write(tmp_path, "c.md", "3")
    got, now = det.changes(first)
    assert (got.added, got.modified, got.removed) == (("c.md",), ("a.md",), ("b.md",))
    assert got.paths == ("a.md", "b.md", "c.md") and got
    again, _ = det.changes(now)
    assert not again and again.paths == ()


def _later() -> int:
    """A clock far past every file's mtime, so the cache is trusted (see the racy tests)."""
    return 2**62


def test_a_file_whose_stat_is_unchanged_is_not_read_again(tmp_path):
    _write(tmp_path, "a.md", "one")
    _write(tmp_path, "b.md", "two")
    counting = Counting()
    det = C.ChangeDetector(tmp_path, hasher=counting, now_ns=_later)
    det.scan()
    assert sorted(counting.reads) == ["a.md", "b.md"]
    counting.reads.clear()
    det.scan()
    assert counting.reads == [], "an unchanged scan costs one stat per file"
    _write(tmp_path, "a.md", "changed!")
    det.scan()
    assert counting.reads == ["a.md"]


def test_a_same_size_rewrite_with_a_restored_mtime_is_noticed_by_its_ctime(tmp_path):
    """Mutant: the signature without the change time. `cp -p`, `tar -x` and `git checkout`
    put the old mtime back; the ctime cannot be put back."""
    _write(tmp_path, "a.md", "aaa")
    old = (tmp_path / "a.md").stat()
    det = C.ChangeDetector(tmp_path, now_ns=_later)
    before = det.scan()
    time.sleep(0.05)  # past the kernel's coarse timestamp tick
    _write(tmp_path, "a.md", "bbb")
    os.utime(tmp_path / "a.md", ns=(old.st_atime_ns, old.st_mtime_ns))
    got, _ = det.changes(before)
    assert got.modified == ("a.md",)


def test_a_file_modified_just_before_it_was_hashed_is_hashed_again(tmp_path):
    """Mutant: trusting the cache for a racily clean file. Two writes inside one timestamp
    tick look alike to a stat; the file is read again until it has aged past RACY_NS."""
    _write(tmp_path, "a.md", "one")
    counting = Counting()
    clock = {"now": (tmp_path / "a.md").stat().st_mtime_ns + 1_000}
    det = C.ChangeDetector(tmp_path, hasher=counting, now_ns=lambda: clock["now"])
    det.scan()
    det.scan()
    assert counting.reads == ["a.md", "a.md"], "too fresh to trust"
    clock["now"] += C.RACY_NS
    det.scan()
    det.scan()
    assert counting.reads == ["a.md", "a.md", "a.md"], "aged: hashed once more, then trusted"


def test_exclude_prune_symlinks_and_unreadable_files(tmp_path):
    _write(tmp_path, "keep/a.md", "1")
    _write(tmp_path, "keep/skip.md", "2")
    _write(tmp_path, "node_modules/x.md", "3")
    (tmp_path / "keep" / "link.md").symlink_to(tmp_path / "keep" / "a.md")
    det = C.ChangeDetector(
        tmp_path, ("**/*.md",), exclude=("keep/skip.md",), prune=(".git", "node_modules")
    )
    assert sorted(det.scan()) == ["keep/a.md"]
    broken = C.ChangeDetector(tmp_path, ("keep/a.md",), hasher=lambda p: None)
    assert broken.scan() == {}, "an unreadable file is left out, not hashed as empty"


def test_a_vanished_file_leaves_the_unread_again_cache(tmp_path):
    _write(tmp_path, "a.md", "1")
    det = C.ChangeDetector(tmp_path)
    det.scan()
    (tmp_path / "a.md").unlink()
    assert det.scan() == {} and det._seen == {}


def test_matches_uses_the_claim_glob_meaning():
    assert C.matches("docs/a.md", ["docs/**"]) and C.matches("x", [])
    assert not C.matches("docs/sub/a.md", ["docs/*.md"]), "`*` stops at `/`"
    assert C.matches("docs/sub/a.md", ["docs/**/*.md"])


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.org")
    _git(tmp_path, "config", "user.name", "t")
    _write(tmp_path, "a.py", "1\n")
    _write(tmp_path, "docs/d.md", "1\n")
    _write(tmp_path, "gone.txt", "1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def test_changed_since_lists_committed_staged_unstaged_and_untracked(repo):
    base = _git(repo, "rev-parse", "HEAD")
    assert C.changed_since(repo, base) == []
    _write(repo, "a.py", "2\n")
    _git(repo, "commit", "-qam", "edit a")
    _write(repo, "docs/d.md", "2\n")
    _git(repo, "add", "docs/d.md")
    (repo / "gone.txt").unlink()
    _write(repo, "new file.txt", "n\n")
    _write(repo, "café.txt", "n\n")
    assert C.changed_since(repo, base) == sorted(
        ["a.py", "docs/d.md", "gone.txt", "new file.txt", "café.txt"]
    )
    assert C.changed_since(repo, base, ["docs/**"]) == ["docs/d.md"]
    assert C.changed_since(repo, base, exclude=["*.txt", "docs/**"]) == ["a.py"]


def test_changed_since_counts_both_sides_of_a_rename(repo):
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "mv", "a.py", "b.py")
    assert C.changed_since(repo, base) == ["a.py", "b.py"]


def test_a_git_failure_is_unknown_never_unchanged(repo):
    assert C.changed_since(repo, "0" * 40) is None
    assert C.changed_since(repo / "docs" / "nope", "HEAD") is None

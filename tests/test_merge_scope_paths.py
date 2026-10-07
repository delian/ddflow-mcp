"""Bug B20dc45f4c5: merge's changed-path check (`_outside_globs`) listed the landing's paths
with renames on and without `-z`. The old path of a renamed file was never listed, and a
non-ASCII name came back C-quoted (`"na\\303\\257ve.py"`), matched no glob and was reported
as out of scope although the item declared it."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

from ddflow.api.lifecycle.merge import _outside_globs


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "old.py").write_text("x = 1\n" * 20)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "checkout", "-qb", "work")
    _git(repo, "mv", "old.py", "new.py")
    (repo / "naïve.py").write_text("y = 2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "rename and a non-ASCII name")
    return repo


def test_a_declared_non_ascii_path_is_in_scope(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    it = SimpleNamespace(globs=["old.py", "new.py", "naïve.py"])
    assert _outside_globs(repo, it, "main", "work") == []


def test_the_old_path_of_a_rename_is_a_changed_path(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    it = SimpleNamespace(globs=["new.py", "naïve.py"])
    assert _outside_globs(repo, it, "main", "work") == ["old.py"]


def test_a_listing_git_could_not_make_is_unknown_not_empty(tmp_path: Path) -> None:
    """Bug B4e42502034: a failed listing read as "nothing outside the globs"."""
    from ddflow.api.lifecycle.merge import _scope_fields

    repo = _repo(tmp_path)
    it = SimpleNamespace(globs=["new.py"])
    assert _outside_globs(repo, it, "no-such-base", "work") is None
    assert _scope_fields(None) == {"outside_globs": [], "outside_globs_unknown": True}
    assert _scope_fields([]) == {"outside_globs": [], "outside_globs_unknown": False}


def test_an_own_worktree_landing_says_its_scope_was_not_listed(tmp_path: Path) -> None:
    """roborev on 2b1d60fd: an own-worktree merge lists nothing, so it must not claim a
    checked, clean scope (`false`); it says null."""
    from ddflow.api.lifecycle.merge import NOT_LISTED, _scope_fields

    assert _scope_fields(NOT_LISTED) == {"outside_globs": [], "outside_globs_unknown": None}

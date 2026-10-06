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

"""A linked worktree gets its own `.venv`, so a `uv pip install -e .`/`uv run` inside
it cannot repoint the primary checkout's shared one.

Found 2026-10-31 (bug B4e7af0f14f): the shared primary `.venv` editable install
(`_editable_impl_ddflow_mcp.pth`) was repointed at an agent worktree; when ddflow merged
and removed that worktree, every ddflow console-script call failed with ModuleNotFoundError
until the `.pth` was set back to the primary checkout. A worktree sits under the primary,
so a worktree with no `.venv` of its own walks up the directory tree on an editable
install and rewrites that `.pth` to point at the doomed tree.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from ddflow.infra import worktree as W


def _uv(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "uv.lock").write_text("")
    return root


def test_a_uv_worktree_gets_its_own_venv(tmp_path):
    """The regression guard: `isolate_venv` gives a uv project's worktree a `.venv` of its
    own, which `uv pip`'s discovery resolves instead of the primary `.venv`."""
    if shutil.which("uv") is None:
        pytest.skip("uv not on PATH")
    root = _uv(tmp_path / "prj")
    tree = tmp_path / "wt"
    tree.mkdir()
    assert W.isolate_venv(root, tree) is True
    assert (tree / ".venv" / "pyvenv.cfg").is_file(), "the worktree has no .venv of its own"


def test_a_worktree_with_a_real_venv_is_left_alone(tmp_path):
    root = _uv(tmp_path / "prj")
    tree = tmp_path / "wt"
    (tree / ".venv").mkdir(parents=True)
    (tree / ".venv" / "pyvenv.cfg").write_text("home = /usr\n")
    assert W.isolate_venv(root, tree) is False
    assert (tree / ".venv" / "pyvenv.cfg").is_file()


def test_a_torn_venv_leftover_is_repaired(tmp_path):
    """An empty `.venv` (no `pyvenv.cfg`) is not a virtualenv — it is the leftover of a
    create that died mid-write. `isolate_venv` repairs it instead of skipping the
    invalid directory and leaving discovery able to reach the primary `.venv`."""
    if shutil.which("uv") is None:
        pytest.skip("uv not on PATH")
    root = _uv(tmp_path / "prj")
    tree = tmp_path / "wt"
    (tree / ".venv").mkdir(parents=True)
    assert W.isolate_venv(root, tree) is True
    assert (tree / ".venv" / "pyvenv.cfg").is_file(), "the torn leftover was not repaired"


def test_a_symlinked_venv_is_not_treated_as_isolated(tmp_path):
    """A `.venv` that links to another (shared) venv is not local isolation — the symlink
    itself must be replaced, even though `pyvenv.cfg` is visible through it."""
    if shutil.which("uv") is None:
        pytest.skip("uv not on PATH")
    root = _uv(tmp_path / "prj")
    shared = tmp_path / "shared"
    (shared / ".venv").mkdir(parents=True)
    (shared / ".venv" / "pyvenv.cfg").write_text("home = /usr\n")
    tree = tmp_path / "wt"
    tree.mkdir()
    (tree / ".venv").symlink_to(shared / ".venv", target_is_directory=True)
    assert W.isolate_venv(root, tree) is True
    assert (tree / ".venv").is_dir() and not (tree / ".venv").is_symlink()
    assert (tree / ".venv" / "pyvenv.cfg").is_file()


def test_a_project_that_is_not_uv_is_not_touched(tmp_path):
    """Python/uv only: told by `uv.lock` beside the primary (the `gates.testcmd` signal).
    A Go/JS project has no `.venv` to repoint, and `uv` may not even be installed there."""
    root = tmp_path / "go"
    root.mkdir()
    tree = tmp_path / "wt"
    tree.mkdir()
    assert W.isolate_venv(root, tree) is False
    assert not (tree / ".venv").exists()

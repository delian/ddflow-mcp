"""Launch lines written from a linked worktree of ddflow point at its primary checkout.

Found onboarding home-simulator (2026-09-29): `ddflow adopt` run from a ddflow worktree
wrote that worktree's path into the project's `.mcp.json` and git hooks. The worktree is
removed when its branch merges; from then on the MCP server cannot import and every git
hook fails closed. Agent shells here export `PYTHONPATH=':'`, so a bare `ddflow` run inside
a worktree IS that worktree's code -- the trap is one `cd` away.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.infra import paths as PATHS
from ddflow.services import adopt as A
from ddflow.services import enforce as E


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


@pytest.fixture
def checkout(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    """A ddflow-shaped primary checkout and a linked worktree of it, with ddflow
    'running from' the worktree."""
    primary = tmp_path / "ddflow"
    (primary / "ddflow").mkdir(parents=True)
    (primary / "ddflow" / "__init__.py").write_text("")
    _git(primary, "init", "-q", "-b", "main")
    _git(primary, "-c", "user.email=t@e", "-c", "user.name=t", "add", ".")
    _git(primary, "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "x")
    tree = tmp_path / "wt"
    _git(primary, "worktree", "add", "-q", str(tree), "-b", "feature")
    monkeypatch.delenv(PATHS.LAUNCH_ROOT_ENV, raising=False)
    monkeypatch.setattr(PATHS, "package_parent", lambda: tree)
    return primary.resolve(), tree


def test_the_mcp_entry_points_at_the_primary_checkout(checkout):
    primary, tree = checkout
    entry = A._launch_entry("python")
    assert entry["env"]["PYTHONPATH"] == str(primary), entry
    assert str(tree) not in str(entry)


def test_the_git_and_session_hooks_point_at_the_primary_checkout(checkout, monkeypatch):
    primary, tree = checkout
    monkeypatch.setattr(E, "_running_from_source", lambda: True)
    line = E.command_line("hooks check-commit")
    assert f'PYTHONPATH="{primary}' in line and str(tree) not in line, line


def test_a_venv_inside_the_worktree_is_replaced_by_the_primarys(checkout, monkeypatch):
    primary, tree = checkout
    venv_py = primary / ".venv" / "bin" / "python3"
    venv_py.parent.mkdir(parents=True)
    venv_py.write_text("")
    monkeypatch.setattr(sys, "executable", str(tree / ".venv" / "bin" / "python3"))
    assert PATHS.launch_python() == str(venv_py)
    assert A._launch_entry("python")["command"] == str(venv_py)


def test_it_says_so(checkout):
    primary, tree = checkout
    note = E.redirect_note()
    assert str(tree) in note and str(primary) in note


def test_the_primary_itself_and_an_explicit_root_are_left_alone(checkout, monkeypatch):
    primary, _tree = checkout
    monkeypatch.setattr(PATHS, "package_parent", lambda: primary)
    assert PATHS.launch_parent() == primary and E.redirect_note() == ""
    monkeypatch.setenv(PATHS.LAUNCH_ROOT_ENV, "/somewhere/else")
    assert PATHS.launch_parent() == Path("/somewhere/else")


def test_a_worktree_of_a_repository_that_is_not_ddflow_is_left_alone(tmp_path, monkeypatch):
    """Only a primary that holds the package is a place to point at."""
    primary = tmp_path / "other"
    primary.mkdir()
    _git(primary, "init", "-q", "-b", "main")
    _git(
        primary,
        "-c",
        "user.email=t@e",
        "-c",
        "user.name=t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "x",
    )
    tree = tmp_path / "wt"
    _git(primary, "worktree", "add", "-q", str(tree), "-b", "f")
    assert PATHS.primary_checkout(tree) is None

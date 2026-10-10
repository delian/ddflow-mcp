"""Launch lines written from a linked worktree of ddflow point at its primary checkout.

Found onboarding home-simulator (2026-09-29): `ddflow adopt` run from a ddflow worktree
wrote that worktree's path into the project's `.mcp.json` and git hooks. The worktree is
removed when its branch merges; from then on the MCP server cannot import and every git
hook fails closed. Agent shells here export `PYTHONPATH=':'`, so a bare `ddflow` run inside
a worktree IS that worktree's code -- the trap is one `cd` away.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers import git_quiet as _git

from ddflow.infra import paths as PATHS
from ddflow.services import adopt as A
from ddflow.services import enforce as E


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


# -- the rubber-duck's findings ----------------------------------------------------------


def test_a_symlinked_path_to_the_worktree_venv_is_still_swapped(checkout, monkeypatch, tmp_path):
    """`/home/delian/src` and `/ai/delian/src` are the same tree here."""
    primary, tree = checkout
    (primary / ".venv" / "bin").mkdir(parents=True)
    (primary / ".venv" / "bin" / "python3").write_text("")
    alias = tmp_path / "alias"
    alias.symlink_to(tree)
    monkeypatch.setattr(sys, "executable", str(alias / ".venv" / "bin" / "python3"))
    assert PATHS.launch_python() == str(primary / ".venv" / "bin" / "python3")


def test_a_bare_common_dir_beside_an_unrelated_package_is_not_a_primary(tmp_path):
    bare = tmp_path / "ddflow.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    seed = tmp_path / "seed"
    _git(tmp_path, "init", "-q", "-b", "main", str(seed))
    _git(
        seed,
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
    _git(seed, "push", "-q", str(bare), "main")
    (tmp_path / "ddflow").mkdir()
    (tmp_path / "ddflow" / "__init__.py").write_text("")
    tree = tmp_path / "wt"
    # A new branch: `main` is the bare repo's HEAD, which some git setups refuse to add.
    _git(bare, "worktree", "add", "-q", "-b", "wt", str(tree), "main")
    assert PATHS.primary_checkout(tree) is None


def test_the_session_start_hook_install_says_so_too(checkout, tmp_path, monkeypatch):
    from ddflow.api import setup as S

    primary, tree = checkout
    monkeypatch.setattr(E, "_running_from_source", lambda: True)
    repo = tmp_path / "proj"
    _git(tmp_path, "init", "-q", "-b", "main", str(repo))
    (repo / ".ddflow").mkdir()
    (repo / ".ddflow" / "config.toml").write_text("")
    out = S.hooks(repo, action="install", claude=True)
    assert str(tree) in out.data["message"] and str(primary) in out.data["message"]


def test_a_launch_line_with_no_path_gets_no_note(checkout, tmp_path):
    repo = tmp_path / "proj"
    _git(tmp_path, "init", "-q", "-b", "main", str(repo))
    actions = A.adopt(repo, agents=["claude"], launch="docker", install_hooks=False)
    assert not any("NOTE: ddflow is running from the linked worktree" in a for a in actions)


def test_an_interpreter_that_cannot_follow_is_warned_about(checkout, monkeypatch):
    _primary, tree = checkout
    monkeypatch.setattr(sys, "executable", str(tree / ".venv" / "bin" / "python3"))
    assert "WARNING" in E.redirect_note() and "no .venv" in E.redirect_note()


def test_an_explicit_root_is_not_reported_as_a_worktree_redirect(checkout, monkeypatch):
    primary, _tree = checkout
    monkeypatch.setattr(PATHS, "package_parent", lambda: primary)
    monkeypatch.setenv(PATHS.LAUNCH_ROOT_ENV, str(primary.parent / "elsewhere"))
    assert "linked worktree" not in E.redirect_note()


def test_an_explicit_root_with_no_package_is_warned_about(checkout, monkeypatch, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv(PATHS.LAUNCH_ROOT_ENV, str(empty))
    assert "no ddflow package" in E.redirect_note()


def test_a_line_that_embeds_no_path_gets_no_note(checkout):
    assert E.redirect_note('exec "/usr/bin/ddflow" hooks check-commit') == ""


def test_a_swapped_interpreter_is_named(checkout, monkeypatch):
    primary, tree = checkout
    py = primary / ".venv" / "bin" / "python3"
    py.parent.mkdir(parents=True)
    py.write_text("")
    monkeypatch.setattr(sys, "executable", str(tree / ".venv" / "bin" / "python3"))
    assert str(py) in E.redirect_note()


def test_with_no_primary_venv_an_outside_interpreter_that_imports_it_is_used(checkout, monkeypatch):
    """The critic: a primary with no `.venv` left the worktree's own interpreter in the
    line. An interpreter outside the worktree that can import the primary's server --
    here the one running this test, after the primary is given that module -- lives on."""
    primary, tree = checkout
    (primary / "ddflow" / "surfaces").mkdir()
    (primary / "ddflow" / "surfaces" / "__init__.py").write_text("")
    (primary / "ddflow" / "surfaces" / "mcp.py").write_text("")
    outside = sys.executable
    monkeypatch.setattr(sys, "executable", str(tree / ".venv" / "bin" / "python3"))
    monkeypatch.setattr(sys, "_base_executable", outside, raising=False)
    assert PATHS.launch_python() == outside
    assert "WARNING" not in E.redirect_note()


def test_an_undecodable_gitdir_pointer_is_no_primary(tmp_path):
    tree = tmp_path / "wt"
    tree.mkdir()
    (tree / ".git").write_bytes(b"gitdir: /tmp/\xff\xfe/worktrees/x\n")
    assert PATHS.primary_checkout(tree) is None


def test_a_probed_interpreter_is_not_called_the_primarys(checkout, monkeypatch):
    primary, tree = checkout
    (primary / "ddflow" / "surfaces").mkdir()
    (primary / "ddflow" / "surfaces" / "__init__.py").write_text("")
    (primary / "ddflow" / "surfaces" / "mcp.py").write_text("")
    outside = sys.executable
    monkeypatch.setattr(sys, "executable", str(tree / ".venv" / "bin" / "python3"))
    monkeypatch.setattr(sys, "_base_executable", outside, raising=False)
    note = E.redirect_note()
    assert f"the interpreter {outside}" in note and "primary's interpreter" not in note


def test_a_venv_symlinked_from_the_worktree_is_written_by_its_real_path(
    checkout, monkeypatch, tmp_path
):
    """`/wt/.venv -> /shared/venv`: the venv outlives the worktree, the path through the
    worktree does not."""
    _primary, tree = checkout
    shared = tmp_path / "shared" / "venv"
    (shared / "bin").mkdir(parents=True)
    (shared / "bin" / "python3").write_text("")
    (tree / ".venv").symlink_to(shared)
    monkeypatch.setattr(sys, "executable", str(tree / ".venv" / "bin" / "python3"))
    assert PATHS.launch_python() == str(shared.resolve() / "bin" / "python3")


def test_an_unreadable_pointer_falls_back_to_the_package_itself(tmp_path, monkeypatch):
    """`launch_parent()` never yields None: no primary means the package's own tree."""
    tree = tmp_path / "wt"
    tree.mkdir()
    (tree / ".git").write_bytes(b"gitdir: /tmp/\xff/worktrees/x\n")
    monkeypatch.delenv(PATHS.LAUNCH_ROOT_ENV, raising=False)
    monkeypatch.setattr(PATHS, "package_parent", lambda: tree)
    assert PATHS.launch_parent() == tree


def test_an_explicit_root_overrides_a_real_worktree_without_calling_it_a_redirect(
    checkout, monkeypatch, tmp_path
):
    _primary, _tree = checkout
    other = tmp_path / "other"
    (other / "ddflow").mkdir(parents=True)
    (other / "ddflow" / "__init__.py").write_text("")
    monkeypatch.setenv(PATHS.LAUNCH_ROOT_ENV, str(other))
    assert PATHS.launch_parent() == other
    assert "linked worktree" not in E.redirect_note()


def test_a_shared_venv_reached_through_an_aliased_worktree_path_is_written_real(
    checkout, monkeypatch, tmp_path
):
    _primary, tree = checkout
    shared = tmp_path / "shared2" / "venv"
    (shared / "bin").mkdir(parents=True)
    (shared / "bin" / "python3").write_text("")
    (tree / ".venv").symlink_to(shared)
    alias = tmp_path / "alias2"
    alias.symlink_to(tree)
    monkeypatch.setattr(sys, "executable", str(alias / ".venv" / "bin" / "python3"))
    assert PATHS.launch_python() == str(shared.resolve() / "bin" / "python3")

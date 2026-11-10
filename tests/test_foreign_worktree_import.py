"""A running ddflow that imported from a foreign lane's worktree is detected, and the
shared editable install is repointed back at the primary.

Found 2026-11-05 on B-hy-stop-protocol (bug B-cli-ran-code-from-another-lanes-worktree):
a `ddflow review` imported its code from `.ddflow/worktrees/fix-B-ci-guards-unran-blocks-red-main`,
a DIFFERENT lane's tree, because the shared venv's editable install (`.pth`/`direct_url.json`)
had been repointed there; when that tree merged and was removed a lazy re-export raised
ModuleNotFoundError. Importing from the caller's OWN tree is legitimate; importing from any
other linked worktree is not — it runs another lane's unmerged code that dies when the
tree goes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers import git_quiet as _git

from ddflow.infra import paths as PATHS
from ddflow.services import install_info as I


@pytest.fixture
def checkout(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    """A ddflow-shaped primary checkout and a linked worktree of it."""
    primary = tmp_path / "ddflow"
    (primary / "ddflow").mkdir(parents=True)
    (primary / "ddflow" / "__init__.py").write_text("")
    _git(primary, "init", "-q", "-b", "main")
    _git(primary, "-c", "user.email=t@e", "-c", "user.name=t", "add", ".")
    _git(primary, "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "x")
    tree = tmp_path / "wt"
    _git(primary, "worktree", "add", "-q", str(tree), "-b", "feature")
    monkeypatch.setattr(PATHS, "package_parent", tree.resolve)
    return primary.resolve(), tree.resolve()


def test_running_from_a_linked_worktree_is_detected_as_foreign(checkout):
    primary, tree = checkout
    foreign = I.foreign_launch_root()
    assert foreign is not None
    assert foreign.worktree == tree
    assert foreign.primary == primary


def test_running_from_the_primary_itself_is_not_foreign(checkout, monkeypatch):
    primary, _tree = checkout
    monkeypatch.setattr(PATHS, "package_parent", lambda: primary)
    assert I.foreign_launch_root() is None


def test_a_non_ddflow_repository_worktree_is_not_foreign(tmp_path, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-q", "-b", "main")
    _git(
        other,
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
    _git(other, "worktree", "add", "-q", str(tree), "-b", "f")
    monkeypatch.setattr(PATHS, "package_parent", tree.resolve)
    assert I.foreign_launch_root() is None


def _editable_site(tmp_path: Path, tree: Path) -> Path:
    site = tmp_path / "site-packages"
    site.mkdir(parents=True)
    info = site / "ddflow_mcp-0.2.10.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: ddflow-mcp\nVersion: 0.2.10\n")
    (info / "direct_url.json").write_text(
        json.dumps({"url": tree.as_uri(), "dir_info": {"editable": True}})
    )
    (site / "_editable_impl_ddflow_mcp.pth").write_text(str(tree) + "\n")
    return site


def test_the_shared_editable_install_is_repointed_at_the_primary(tmp_path, monkeypatch):
    primary = tmp_path / "prj"
    tree = tmp_path / "wt"
    tree.mkdir()
    site = _editable_site(tmp_path, tree)
    monkeypatch.syspath_prepend(str(site))
    assert I.repoint_shared_editable(tree, primary) is True
    assert (site / "_editable_impl_ddflow_mcp.pth").read_text().strip() == str(primary)
    record = json.loads((site / "ddflow_mcp-0.2.10.dist-info" / "direct_url.json").read_text())
    assert record["url"] == primary.as_uri()
    assert record["dir_info"] == {"editable": True}


def test_a_primary_pointing_editable_install_is_left_alone(tmp_path, monkeypatch):
    primary = tmp_path / "prj"
    tree = tmp_path / "other-tree"
    tree.mkdir()
    site = _editable_site(tmp_path, primary)
    monkeypatch.syspath_prepend(str(site))
    assert I.repoint_shared_editable(tree, primary) is False
    assert (site / "_editable_impl_ddflow_mcp.pth").read_text().strip() == str(primary)


def test_the_pth_is_repointed_through_a_symlinked_path(tmp_path, monkeypatch):
    """The `.pth` names the import root as the installer wrote it, possibly through a
    symlink (`/tmp` -> `/private/tmp`): repoint matches it RESOLVED or the refusal that
    heals the URL strands the foreign tree and locks the CLI out."""
    real = tmp_path / "real"
    real.mkdir()
    tree = tmp_path / "ln"
    tree.symlink_to(real)
    primary = tmp_path / "prj"
    site = _editable_site(tmp_path, tree)
    monkeypatch.syspath_prepend(str(site))
    assert I.repoint_shared_editable(tree.resolve(), primary) is True
    assert (site / "_editable_impl_ddflow_mcp.pth").read_text().strip() == str(primary)


def test_the_cli_guard_allows_the_callers_own_tree(checkout, monkeypatch):
    """The bug says the caller's OWN tree is legitimate; the guard must not touch it."""
    from ddflow.infra import worktree as W
    from ddflow.surfaces import cli as CLI

    _primary, tree = checkout
    monkeypatch.setattr(PATHS, "package_parent", lambda: tree)
    monkeypatch.setattr(W, "current", lambda start: W.Worktree(item="", path=tree, branch="b"))
    assert CLI._guard_foreign_launch() == CLI.OK


def test_the_cli_guard_refuses_a_foreign_tree_and_heals_it(checkout, monkeypatch):
    """Standing in the PRIMARY while importing from a DIFFERENT linked worktree is the
    foreign case this bug records: the guard repoints and refuses instead of running it."""
    from ddflow.infra import worktree as W
    from ddflow.surfaces import cli as CLI

    _primary, tree = checkout
    monkeypatch.setattr(PATHS, "package_parent", lambda: tree)
    monkeypatch.setattr(W, "current", lambda start: None)  # caller at the primary
    monkeypatch.setattr(I, "repoint_shared_editable", lambda foreign, primary: False)
    assert CLI._guard_foreign_launch() == CLI.FAIL


def test_identifying_the_caller_failing_is_not_a_refusal(checkout, monkeypatch):
    from ddflow.infra import worktree as W
    from ddflow.surfaces import cli as CLI

    _primary, tree = checkout
    monkeypatch.setattr(PATHS, "package_parent", lambda: tree)

    def broken(_start):
        raise OSError("cwd removed")

    monkeypatch.setattr(W, "current", broken)
    assert CLI._guard_foreign_launch() == CLI.OK

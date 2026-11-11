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


def test_running_from_a_linked_worktree_is_detected_with_its_primary(checkout):
    """The detector names the worktree the package imported from and its primary; that it
    is FOREIGN (not the caller's own tree) is the CLI guard's decision, tested below."""
    primary, tree = checkout
    launch = I.foreign_launch_root()
    assert launch is not None
    assert launch.worktree == tree
    assert launch.primary == primary


def test_running_from_the_primary_itself_is_not_foreign(checkout, monkeypatch):
    primary, _tree = checkout
    monkeypatch.setattr(PATHS, "package_parent", lambda: primary)
    assert I.foreign_launch_root() is None


def test_ddflows_own_scratch_tree_is_not_foreign(checkout, monkeypatch, tmp_path):
    """A ``ddflow-*`` tree directly under the temp dir is ddflow's OWN throwaway scratch,
    not another lane's unmerged branch: importing from it is legitimate (B267ff88849 — the
    ci pre-push run lives in one). A lane's tree is never a sibling of the temp root."""
    import tempfile

    primary, _tree = checkout
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    scratch = tmp_path / "ddflow-ci.abc123"
    _git(primary, "worktree", "add", "-q", str(scratch), "--detach")
    monkeypatch.setattr(PATHS, "package_parent", scratch.resolve)
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


@pytest.mark.real_foreign_guard
def test_the_cli_guard_allows_the_callers_own_tree(checkout, monkeypatch):
    """The bug says the caller's OWN tree is legitimate; the guard must not touch it."""
    from ddflow.infra import worktree as W
    from ddflow.surfaces import cli as CLI

    _primary, tree = checkout
    monkeypatch.setattr(PATHS, "package_parent", lambda: tree)
    monkeypatch.setattr(
        W, "current", lambda start: W.Worktree(item="", path=tree, branch="b", base="")
    )
    assert CLI._guard_foreign_launch() == CLI.OK


@pytest.mark.real_foreign_guard
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


@pytest.mark.real_foreign_guard
def test_identifying_the_caller_failing_refuses_without_repointing(checkout, monkeypatch):
    from ddflow.infra import worktree as W
    from ddflow.surfaces import cli as CLI

    _primary, tree = checkout
    monkeypatch.setattr(PATHS, "package_parent", lambda: tree)

    def broken(_start):
        raise OSError("cwd removed")

    monkeypatch.setattr(W, "current", broken)
    monkeypatch.setattr(I, "repoint_shared_editable", lambda foreign, primary: True)
    assert CLI._guard_foreign_launch() == CLI.FAIL


# --- the guard under a sandboxed TMPDIR, an explicit launch and a genuine foreign launch ---


def test_a_scratch_tree_is_not_foreign_when_the_process_has_its_own_tmpdir(
    checkout, monkeypatch, tmp_path
):
    """Bbaaaba1364: ci's tree is made in the real system temp dir, but a test (the swarm
    pilot's sandbox) runs ddflow with TMPDIR pointed elsewhere, so the process's own temp
    root is NOT the scratch tree's parent. The tree is ddflow's own scratch all the same."""
    primary, _tree = checkout
    system_tmp = tmp_path / "systmp"
    system_tmp.mkdir()
    scratch = system_tmp / "ddflow-ci.abc123"
    _git(primary, "worktree", "add", "-q", str(scratch), "--detach")
    sandbox_tmp = tmp_path / "sandbox-tmp"
    sandbox_tmp.mkdir()
    monkeypatch.setenv("TMPDIR", str(sandbox_tmp))
    monkeypatch.setattr(I, "SYSTEM_TEMP_DIRS", (system_tmp.resolve(),))
    monkeypatch.setattr(PATHS, "package_parent", scratch.resolve)
    assert I.foreign_launch_root() is None


def test_a_ddflow_named_lane_tree_outside_the_temp_dirs_is_still_foreign(
    checkout, monkeypatch, tmp_path
):
    """The scratch exemption is the temp dir's direct children only: a `ddflow-` name
    elsewhere (a lane tree under .ddflow/worktrees) is another lane's code."""
    primary, _tree = checkout
    lane = tmp_path / "lanes" / "ddflow-looks-like-scratch"
    lane.parent.mkdir()
    _git(primary, "worktree", "add", "-q", str(lane), "--detach")
    monkeypatch.setattr(I, "SYSTEM_TEMP_DIRS", (tmp_path / "elsewhere",))
    monkeypatch.setattr(PATHS, "package_parent", lane.resolve)
    assert I.foreign_launch_root() is not None


def _outsider(tmp_path: Path) -> Path:
    """A repository unrelated to the ddflow checkout (a test's sandbox)."""
    box = tmp_path / "sandbox"
    box.mkdir()
    _git(box, "init", "-q", "-b", "main")
    return box


def test_an_explicit_pythonpath_launch_from_an_unrelated_repository_is_allowed(
    checkout, monkeypatch, tmp_path
):
    """Bc28808f9c3 / Bbaaaba1364: a launcher that put the tree on PYTHONPATH chose its code
    on purpose, and a caller in a repository that is not this one's family has no lane the
    code could be foreign to (the swarm pilot, a pin, an installed-adopt run)."""
    from ddflow.api.guard_launch import guard_foreign_launch
    from ddflow.core.outcome import OK

    _primary, tree = checkout
    monkeypatch.setattr(PATHS, "package_parent", lambda: tree)
    monkeypatch.setenv("PYTHONPATH", str(tree))
    monkeypatch.chdir(_outsider(tmp_path))
    assert guard_foreign_launch() == OK


def test_a_silent_foreign_launch_is_still_refused_from_an_unrelated_repository(
    checkout, monkeypatch, tmp_path, capsys
):
    """No PYTHONPATH names the tree: it came in through the shared editable install, the
    very redirect this guard exists for. Where the caller stands does not excuse it."""
    from ddflow.api.guard_launch import guard_foreign_launch
    from ddflow.core.outcome import FAIL

    _primary, tree = checkout
    monkeypatch.setattr(PATHS, "package_parent", lambda: tree)
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.setattr(I, "repoint_shared_editable", lambda foreign, primary: False)
    monkeypatch.chdir(_outsider(tmp_path))
    assert guard_foreign_launch() == FAIL
    assert "imported from the linked worktree" in capsys.readouterr().err


def test_an_explicit_pythonpath_does_not_excuse_a_launch_from_the_checkouts_own_family(
    checkout, monkeypatch, capsys
):
    """A hook or an agent standing in the primary (or another lane) that runs a lane's code
    by PYTHONPATH is the foreign launch the guard exists for: still refused."""
    from ddflow.api.guard_launch import guard_foreign_launch
    from ddflow.core.outcome import FAIL

    primary, tree = checkout
    monkeypatch.setattr(PATHS, "package_parent", lambda: tree)
    monkeypatch.setenv("PYTHONPATH", str(tree))
    monkeypatch.setattr(I, "repoint_shared_editable", lambda foreign, primary: False)
    monkeypatch.chdir(primary)
    assert guard_foreign_launch() == FAIL
    other = primary.parent / "other-lane"
    _git(primary, "worktree", "add", "-q", str(other), "-b", "other")
    monkeypatch.chdir(other)
    assert guard_foreign_launch() == FAIL

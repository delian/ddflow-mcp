"""A killed ci run leaves its /tmp/ddflow-ci.* scratch tree registered in git's worktree
list, and `cleanup` never even sees it: it lists only .ddflow/worktrees (B43593d1f3c).

The ci gate's scratch tree is created detached under the system temp directory, so it is
on no ddflow branch and no queue item claims it. `git worktree remove` on exit cannot run
when the run is KILLED, and nothing else cleaned it up: the 2026-10-24 restart pass found
83 registered /tmp/ddflow-ci.* entries. The fix:

* a ci run claims its scratch tree in a sibling owner file (`<tree>.owner`: pid and the
  process start time, so a reused pid is not a live owner) and removes tree and
  registration even when a signal ends the run;
* `cleanup` names every ddflow-ci.* worktree, tells a live run's from a leftover whose
  owner is gone, and `--apply` prunes the dead ones.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from conftest import run_cli
from helpers import commit as _commit
from helpers import git_quiet as _git

from ddflow import api
from ddflow.infra import worktree as W
from ddflow.services import ci as CI


def _owner_marker(tree: Path) -> Path:
    return Path(f"{tree}{CI.OWNER_SUFFIX}")


def _leaf_scratch(repo: Path) -> Path:
    """A leftover like a killed ci run's: a detached scratch worktree of the base,
    NOT inside .ddflow/worktrees."""
    path = repo.parent / "ddflow-ci.leftover"
    _git(repo, "worktree", "add", "-q", "--detach", str(path), "HEAD")
    return path


def _row(out, path: Path) -> dict:
    rows = [t for t in out.data["trees"] if Path(t["path"]).resolve() == path.resolve()]
    assert rows, f"cleanup did not report the scratch tree {path}: {out.data['trees']}"
    return rows[0]


def _dead_owner(marker: Path) -> None:
    """An owner that definitely exited: a pid no kernel boots with and never recycles."""
    marker.write_text("999999 0\n")


def test_merge_tree_claims_and_releases_an_owner_marker(repo):
    """The scratch tree of a ci run carries an owner marker while it lives, and none after."""
    run_cli(repo, "init")
    _commit(repo, {"f.txt": "f\n"}, "base")
    with CI.merge_tree(repo, "HEAD", "") as (tree, why):
        assert tree is not None, why
        marker = _owner_marker(tree)
        assert marker.exists(), "the scratch tree does not say who owns it"
        assert marker.read_text().strip() == CI.owner_line(), "the marker does not name THIS pid"
    assert not marker.exists(), "the owner marker outlived the scratch tree"
    assert not tree.exists(), "the scratch tree outlived the run"


def test_survey_names_a_scratch_leftover_whose_owner_is_gone(repo):
    """`cleanup` (the dry run) sees a killed run's ddflow-ci.* tree at all, and offers
    exactly one safe action: prune it."""
    run_cli(repo, "init")
    _commit(repo, {"f.txt": "f\n"}, "base")
    tree = _leaf_scratch(repo)
    _dead_owner(_owner_marker(tree))

    out = api.cleanup(repo, agent="sweeper")

    row = _row(out, tree)
    assert row["kind"] == "scratch", row
    assert row["action"] == "prune_scratch", row
    assert "gone" in row["done"], row


def test_apply_prunes_the_leftover_tree_and_its_registration(repo):
    """`cleanup --apply` frees the tree AND its git worktree registration, so the
    restart-pass list shows no dead ddflow-ci.* entry."""
    run_cli(repo, "init")
    _commit(repo, {"f.txt": "f\n"}, "base")
    tree = _leaf_scratch(repo)
    _dead_owner(_owner_marker(tree))

    out = api.cleanup(repo, apply=True, agent="sweeper")

    assert not tree.exists(), f"the leftover scratch tree survived: {out.data['performed']}"
    registered = next(
        (e for e in W.list_worktrees(repo) if Path(e.get("worktree", "")).resolve() == tree.resolve()),
        None,
    )
    assert registered is None, "git worktree list still registers the leftover"
    assert any("pruned leftover ci scratch tree" in line for line in out.data["performed"])
    assert not _owner_marker(tree).exists(), "the owner marker outlived the prune"


def test_a_live_ci_scratch_tree_is_named_and_kept(repo):
    """A ddflow-ci.* tree whose owner is THIS process is a run in progress, not a leftover:
    named, with no action."""
    run_cli(repo, "init")
    _commit(repo, {"f.txt": "f\n"}, "base")
    tree = _leaf_scratch(repo)
    _owner_marker(tree).write_text(CI.owner_line() + "\n")

    out = api.cleanup(repo, apply=True, agent="sweeper")

    row = _row(out, tree)
    assert tree.exists(), f"a live ci run's scratch tree was removed: {out.data['performed']}"
    assert row["action"] == "", row

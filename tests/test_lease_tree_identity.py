"""An item's own worktree speaks for its lease; another tree does not take it over.

Bug Bd25a0d2adb: `ddflow heartbeat <id>` run inside the worktree `claim` made for that
item said "no lease held". Identity is derived from the tree (`Monster3-B187`) while the
lease was taken from the primary (`Monster3-ddflow`). `check_commit` already counts "the
lease that created this tree" as mine; heartbeat did not, so the one command an agent
runs from where it works could not keep that work's lease alive.

Bug Bce34e64d3b: `ddflow claim <id>` run from a DIFFERENT worktree adopted the caller's
tree and branch, even though the item already recorded its own worktree holding
unmerged commits. A merge from there merges nothing, and the salvage in the recorded
tree is orphaned.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


def _git(where, *args) -> str:
    return subprocess.run(
        ["git", "-C", str(where), *args], check=True, capture_output=True, text=True
    ).stdout


def _item(repo: Path, iid: str = "T1"):
    return fold(EventLog(repo, "reader").read_all(), strict=False).items[iid]


def _claimed_with_its_own_tree(repo: Path) -> Path:
    assert run_cli(repo, "init")[0] == OK
    assert run_cli(repo, "task", "add", "T1", "--globs", "a.py")[0] == OK
    code, out, err = run_cli(repo, "claim", "T1", agent="lead")
    assert code == OK, out + err
    tree = Path(_item(repo).lease.worktree)
    tree = tree if tree.is_absolute() else (repo / tree).resolve()
    assert tree.is_dir(), tree
    return tree


# -- Bd25a0d2adb ------------------------------------------------------------------------


def test_heartbeat_from_the_items_own_tree_renews_its_lease(repo):
    tree = _claimed_with_its_own_tree(repo)
    before = _item(repo).lease.renewed_at
    code, out, err = run_cli(tree, "heartbeat", "T1")  # identity derived from the tree
    assert code == OK, out + err
    after = _item(repo).lease
    assert after.holder == "lead", after.holder  # renewed, not taken over
    assert after.renewed_at >= before


def test_heartbeat_from_an_unrelated_tree_still_refuses(repo):
    """The other half: standing in SOME worktree is not a claim on every lease."""
    _claimed_with_its_own_tree(repo)
    other = repo.parent / "elsewhere"
    _git(repo, "worktree", "add", "-q", str(other), "-b", "elsewhere")
    code, _out, _err = run_cli(other, "heartbeat", "T1")
    assert code == NOTHING


# -- Bce34e64d3b ------------------------------------------------------------------------


def test_a_reclaim_from_another_tree_keeps_the_items_recorded_tree(repo):
    tree = _claimed_with_its_own_tree(repo)
    (tree / "a.py").write_text("salvage\n")
    _git(tree, "add", "a.py")
    _git(tree, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "salvage")
    branch = _item(repo).branch
    assert run_cli(repo, "release", "T1", agent="lead")[0] == OK

    other = repo.parent / "other-tree"
    _git(repo, "worktree", "add", "-q", str(other), "-b", "other-work")
    code, out, err = run_cli(other, "claim", "T1")
    assert code == OK, out + err
    it = _item(repo)
    assert it.branch == branch, (it.branch, out)
    recorded = Path(it.worktree)
    recorded = recorded if recorded.is_absolute() else (repo / recorded).resolve()
    assert recorded.resolve() == tree.resolve(), (recorded, out)
    assert "adopted" not in out, out
    assert str(tree) in out, out


def test_a_first_claim_from_a_harness_tree_is_still_adopted(repo):
    """The adoption B115 introduced stays: an item with NO tree of its own takes yours."""
    assert run_cli(repo, "init")[0] == OK
    assert run_cli(repo, "task", "add", "T1", "--globs", "a.py")[0] == OK
    harness = repo.parent / "harness-tree"
    _git(repo, "worktree", "add", "-q", str(harness), "-b", "harness")
    code, out, err = run_cli(harness, "claim", "T1")
    assert code == OK, out + err
    assert "adopted" in out, out
    assert _item(repo).branch == "harness"


def test_a_reclaim_from_the_primary_keeps_an_adopted_tree_it_did_not_make(repo):
    """A recorded tree may be anywhere, not only where `W.create` would put one."""
    assert run_cli(repo, "init")[0] == OK
    assert run_cli(repo, "task", "add", "T1", "--globs", "a.py")[0] == OK
    harness = repo.parent / "harness-tree"
    _git(repo, "worktree", "add", "-q", str(harness), "-b", "harness")
    assert run_cli(harness, "claim", "T1", agent="lead")[0] == OK
    assert run_cli(repo, "release", "T1", agent="lead")[0] == OK

    code, out, err = run_cli(repo, "claim", "T1")
    assert code == OK, out + err
    it = _item(repo)
    assert it.branch == "harness", (it.branch, out)
    assert (repo / it.lease.worktree).resolve() == harness.resolve(), it.lease.worktree
    assert it.adopted  # still not ddflow's to remove on merge
    assert f"cd {harness.resolve()}" in out, out

"""`cleanup --apply` never removes a tree someone is working in (bug B5a009b185a).

`cleanup` classified a worktree by what git said about it -- clean, nothing ahead of
the base -- and called that "merged, safe to remove". A tree an agent has JUST claimed
is exactly that: branched from the base a second ago, no commits yet, no edits. So
`--apply` deleted a live agent's freshly claimed tree out from under it, the one
direction ddflow promises never to go.

Two kinds of tree are not ddflow's to remove, whatever git says about them:

* one whose item holds a LIVE lease -- an agent is working in it right now;
* one that is ADOPTED (`Item.adopted`) -- the agent harness created it and ddflow only
  bound an item to it. `merge` already refuses to delete one (B930f5c6b7c) and
  `recover` already says to leave it to the harness; `cleanup` was the one path left.

Real git repositories throughout: the property under test is what happens to a
directory on disk, and a mock would let that be wrong while the tests stayed green.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git_quiet as _git

from ddflow import api
from ddflow.core.model import fold
from ddflow.infra.log import EventLog

OK = 0


def _tree_of(repo: Path, item: str) -> Path:
    code, shown, err = run_cli(repo, "--json", "show", item)
    assert code == OK, err
    tree = Path(json.loads(shown)["worktree"])
    if not tree.is_absolute():
        tree = (repo / tree).resolve()
    assert tree.exists(), "the fixture produced no worktree, so this proves nothing"
    return tree


def _harness_tree(repo: Path, branch: str) -> Path:
    """What Claude Code / Cursor do before ddflow is ever called."""
    path = repo.parent / "harness-tree"
    _git(repo, "worktree", "add", "-q", str(path), "-b", branch)
    return path


def _row(out, path: Path):
    rows = [t for t in out.data["trees"] if Path(t["path"]).resolve() == path.resolve()]
    assert rows, f"cleanup did not report {path} at all: {out.data['trees']}"
    return rows[0]


def test_a_live_leased_clean_tree_survives_cleanup_apply(repo):
    """The headline. Claimed a moment ago: clean, nothing ahead -- and someone's."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    code, _, err = run_cli(repo, "claim", "T1", agent="worker")
    assert code == OK, err
    tree = _tree_of(repo, "T1")

    out = api.cleanup(repo, apply=True, agent="sweeper")

    assert tree.exists(), f"cleanup --apply deleted a live-leased tree: {out.data['performed']}"
    row = _row(out, tree)
    assert row["action"] == "", row
    assert row["kind"] == "held", row
    assert "worker" in row["done"], "the report must say WHO holds it"


def test_an_adopted_clean_tree_survives_cleanup_apply_after_its_lease_is_released(repo):
    """No lease protects it any more -- only adoption does. The harness made this tree
    and may still be standing in it; `release` ends ddflow's claim, not the harness's."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    # On a branch carrying ddflow's prefix, so the survey cannot skip it by name.
    tree = _harness_tree(repo, "ddflow/harness-work")
    code, out_, err = run_cli(tree, "claim", "T1", agent="worker")
    assert code == OK, err
    assert "adopted" in out_, out_
    code, _, err = run_cli(repo, "release", "T1", agent="worker")
    assert code == OK, err
    it = fold(EventLog(repo).read_all(), strict=False).items["T1"]
    assert it.adopted and it.lease is None, "fixture: expected an adopted tree, no lease"

    out = api.cleanup(repo, apply=True, agent="sweeper")

    assert tree.exists(), f"cleanup --apply deleted an adopted tree: {out.data['performed']}"
    row = _row(out, tree)
    assert row["action"] == "", row
    assert row["kind"] == "adopted", row


def test_an_adopted_tree_survives_with_an_empty_branch_prefix(repo):
    """With `branch_prefix = ""` the survey sees every linked worktree, the harness's own
    on its own branch name included -- and matching items by branch alone is then all
    that stands between it and `git worktree remove`."""
    run_cli(repo, "init")
    cfg_path = repo / ".ddflow" / "config.toml"
    text = cfg_path.read_text()
    assert "\n[worktree]\n" in text, "fixture: init wrote no [worktree] table to extend"
    cfg_path.write_text(text.replace("\n[worktree]\n", '\n[worktree]\nbranch_prefix = ""\n', 1))
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    tree = _harness_tree(repo, "agent-work")
    code, _, err = run_cli(tree, "claim", "T1", agent="worker")
    assert code == OK, err
    run_cli(repo, "release", "T1", agent="worker")

    out = api.cleanup(repo, apply=True, agent="sweeper")

    assert tree.exists(), f"cleanup --apply deleted an adopted tree: {out.data['performed']}"
    assert _row(out, tree)["action"] == ""
    # With no prefix every branch is a candidate -- the base branch must not be one; it
    # was offered for deletion and survived only because the primary had it checked out.
    offered = [b["branch"] for b in out.data["stale_branches"]]
    assert "main" not in offered, f"the base branch was offered as stale: {offered}"


def test_a_merged_tree_nobody_holds_is_still_removed(repo):
    """The guard must not turn cleanup into a no-op: a released, ddflow-created, merged
    tree is exactly what `--apply` exists to remove."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    run_cli(repo, "claim", "T1", agent="worker")
    tree = _tree_of(repo, "T1")
    code, _, err = run_cli(repo, "release", "T1", agent="worker")
    assert code == OK, err

    out = api.cleanup(repo, apply=True, agent="sweeper")

    assert not tree.exists(), f"a free merged tree was kept: {out.data['trees']}"


def test_a_live_leased_branch_without_a_tree_is_not_deleted(repo):
    """`claim` takes the lease before the tree exists, and `--no-worktree` never makes
    one: a leased branch with no tree is the holder's, not a stale leftover."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    run_cli(repo, "claim", "T1", agent="worker")
    tree = _tree_of(repo, "T1")
    # The tree goes (by hand, say), the lease and the branch stay.
    _git(repo, "worktree", "remove", str(tree))  # git keeps the branch

    out = api.cleanup(repo, apply=True, agent="sweeper")

    branches = subprocess.run(
        ["git", "-C", str(repo), "branch", "--list", "ddflow/T1"],
        capture_output=True,
        text=True,
    ).stdout
    assert "ddflow/T1" in branches, f"a live-leased branch was deleted: {out.data['performed']}"


def test_a_tree_claimed_between_survey_and_apply_survives(repo):
    """The plan is as old as the survey. A claim that lands after it -- here a re-claim
    that picks the same tree back up -- must still win when `apply` gets there."""
    from ddflow.config import Config
    from ddflow.services import cleanup as CL

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    run_cli(repo, "claim", "T1", agent="worker")
    tree = _tree_of(repo, "T1")
    run_cli(repo, "release", "T1", agent="worker")
    cfg = Config.load(repo)
    log = EventLog(repo)
    plan = CL.survey(repo, cfg, fold(log.read_all(), strict=False))
    assert [t.action for t in plan.trees] == ["remove"], "fixture: expected a free tree"

    code, _, err = run_cli(repo, "claim", "T1", agent="worker")
    assert code == OK, err
    assert _tree_of(repo, "T1") == tree, "fixture: the re-claim did not reuse the tree"
    done = CL.apply(repo, cfg, plan, log=log)

    assert tree.exists(), f"a tree claimed after the survey was deleted: {done}"
    assert plan.trees[0].kind == "held", plan.trees[0]


def test_a_claim_racing_the_removal_itself_keeps_its_tree(repo, monkeypatch):
    """Re-reading the queue before each removal only shrinks the window: a claim appended
    after the re-read and before `git worktree remove` still lost its tree (B19d87ac436).
    The re-check and the removal must hold the log's append lock together, so a claim
    either lands first and is seen, or waits and then finds no tree and makes a new one.

    Forced deterministically: the claim is started from INSIDE the removal and given
    seconds to finish before the removal proceeds. Unlocked, it finishes and binds the
    tree about to be deleted; locked, it blocks until the removal is done.
    """
    import threading

    from ddflow.infra import worktree as W

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    run_cli(repo, "claim", "T1", agent="worker")
    _tree_of(repo, "T1")
    run_cli(repo, "release", "T1", agent="worker")

    real_remove = W.remove
    claims: list[tuple[int, str, str]] = []
    racer: list[threading.Thread] = []

    def remove_while_someone_claims(*args, **kwargs):
        t = threading.Thread(
            target=lambda: claims.append(run_cli(repo, "claim", "T1", agent="worker"))
        )
        t.start()
        racer.append(t)
        t.join(timeout=6)
        return real_remove(*args, **kwargs)

    monkeypatch.setattr(W, "remove", remove_while_someone_claims)
    api.cleanup(repo, apply=True, agent="sweeper")
    for t in racer:
        t.join(timeout=60)

    assert claims and claims[0][0] == OK, f"the racing claim did not succeed: {claims}"
    it = fold(EventLog(repo).read_all(), strict=False).items["T1"]
    assert it.lease is not None, "fixture: the racing claim left no lease"
    held = W.load_path(repo, it.worktree)
    assert held.exists(), f"a live lease points at a tree cleanup deleted: {held}"

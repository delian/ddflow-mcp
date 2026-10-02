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


# -- the MCP surface ------------------------------------------------------------------


def _mcp(start: Path, repo: Path, tool: str, **arguments) -> dict:
    import json

    from ddflow.surfaces.mcp import Server

    reply = Server(repo, called_from=start).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }
    )
    result = reply["result"]
    return {"exit": result.get("_meta", {}).get("exit"), **json.loads(result["content"][0]["text"])}


def test_mcp_heartbeat_from_the_items_own_tree_renews_its_lease(repo):
    """The harnesses this was written for drive MCP, where `called_from` is only passed
    to tools that ask for it: the CLI fix alone left `ddflow_heartbeat` saying "no lease
    held" from exactly the tree `claim` made."""
    tree = _claimed_with_its_own_tree(repo)
    res = _mcp(tree, repo, "ddflow_heartbeat", id="T1")
    assert res["exit"] == OK and res["renewed"] is True, res
    assert _item(repo).lease.holder == "lead"


def test_mcp_heartbeat_from_an_unrelated_tree_still_refuses(repo):
    _claimed_with_its_own_tree(repo)
    other = repo.parent / "elsewhere"
    _git(repo, "worktree", "add", "-q", str(other), "-b", "elsewhere")
    res = _mcp(other, repo, "ddflow_heartbeat", id="T1")
    assert res["exit"] == NOTHING and res["renewed"] is False, res


def test_mcp_claim_says_the_item_kept_its_own_tree(repo):
    """Over MCP the only way to learn "cd there" is the payload: without `rebound` and
    `here`, an agent cannot tell its own tree was not the one bound."""
    tree = _claimed_with_its_own_tree(repo)
    assert run_cli(repo, "release", "T1", agent="lead")[0] == OK
    other = repo.parent / "other-tree"
    _git(repo, "worktree", "add", "-q", str(other), "-b", "other-work")
    res = _mcp(other, repo, "ddflow_claim", id="T1")
    assert res["exit"] == OK, res
    assert res.get("rebound") is True and res.get("here") is False, res
    assert Path(res["worktree"]).resolve() == tree.resolve(), res


# -- review findings on the fixes above ------------------------------------------------


def test_heartbeat_from_the_tree_does_not_resurrect_an_expired_lease(repo):
    """Speaking for the lease that made this tree must not revive one that has lapsed:
    that locked out the agent `recover` sent to take the item over."""
    import time

    assert run_cli(repo, "init")[0] == OK
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text().replace("ttl_s = 1800", "ttl_s = 1\ngrace_s = 0", 1))
    assert run_cli(repo, "task", "add", "T1", "--globs", "a.py")[0] == OK
    assert run_cli(repo, "claim", "T1", agent="lead")[0] == OK
    tree = _resolved(repo, _item(repo).lease.worktree)
    before = _item(repo).lease.renewed_at
    time.sleep(2.2)

    code, out, err = run_cli(tree, "heartbeat", "T1", agent="intruder")
    assert code == NOTHING, out + err
    assert "expired" in out + err, out + err
    assert _item(repo).lease.renewed_at == before
    # Still EXPIRED, so the recovery path applies -- not "held by lead", which a
    # resurrected lease answered with.
    code, out, err = run_cli(repo, "claim", "T1", agent="rescuer")
    assert "EXPIRED" in out + err, out + err
    code, out, err = run_cli(repo, "claim", "T1", "--force", agent="rescuer")
    assert code == OK, out + err
    assert _item(repo).lease.holder == "rescuer"


def test_a_reclaim_ignores_a_recorded_path_now_holding_another_branch(repo):
    """A directory at the recorded path is not the item's tree once it was removed and
    re-added on an unrelated branch: binding to it reported `ddflow/T1` over a tree
    where something else is checked out."""
    tree = _claimed_with_its_own_tree(repo)
    assert run_cli(repo, "release", "T1", agent="lead")[0] == OK
    _git(repo, "worktree", "remove", "--force", str(tree))
    _git(repo, "worktree", "add", "-q", str(tree), "-b", "unrelated")

    other = repo.parent / "other-tree"
    _git(repo, "worktree", "add", "-q", str(other), "-b", "other-work")
    code, out, err = run_cli(other, "claim", "T1")
    assert code == OK, out + err
    it = _item(repo)
    assert _resolved(repo, it.lease.worktree) == other.resolve(), (it.lease.worktree, out)
    assert it.branch == "other-work", (it.branch, out)


def test_a_reclaim_from_the_primary_refuses_a_default_path_on_another_branch(repo):
    """The same occupied path reached from the primary: `W.create` reused whatever tree
    sat there and the claim reported the item's branch over someone else's."""
    tree = _claimed_with_its_own_tree(repo)
    assert run_cli(repo, "release", "T1", agent="lead")[0] == OK
    _git(repo, "worktree", "remove", "--force", str(tree))
    _git(repo, "worktree", "add", "-q", str(tree), "-b", "unrelated")

    code, out, err = run_cli(repo, "claim", "T1")
    assert code == REFUSED, out + err
    assert "unrelated" in err, err
    lease = _item(repo).lease
    assert not (lease and lease.holder), lease  # the refusal released it again


def test_a_reclaim_from_the_primary_refuses_a_detached_default_path_of_unrelated_work(
    repo,
):
    """Detached is only the item's tree when it carries the item's branch. An orphan
    commit at the default path was bound as `ddflow/T1` over unrelated work."""
    tree = _claimed_with_its_own_tree(repo)
    assert run_cli(repo, "release", "T1", agent="lead")[0] == OK
    _git(repo, "worktree", "remove", "--force", str(tree))
    empty = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # git's well-known empty tree
    orphan = _git(repo, "commit-tree", empty, "-m", "unrelated").strip()
    _git(repo, "worktree", "add", "-q", "--detach", str(tree), orphan)

    code, out, err = run_cli(repo, "claim", "T1")
    assert code == REFUSED, out + err
    assert "detached" in err, err
    lease = _item(repo).lease
    assert not (lease and lease.holder), lease


def test_a_refused_reclaim_keeps_the_lease_the_caller_already_held(repo):
    """A refusal releases only what the refused claim acquired. The holder re-claiming
    from a tree bound to another open item only RENEWED its lease -- releasing it made
    the item claimable by anyone while the holder's work sat in it. A fresh claim that
    is refused still leaks nothing."""
    assert run_cli(repo, "init")[0] == OK
    for iid, g in (("T1", "a.py"), ("T2", "b.py"), ("T3", "c.py")):
        assert run_cli(repo, "task", "add", iid, "--globs", g)[0] == OK
    assert run_cli(repo, "claim", "T1", "--no-worktree", agent="lead")[0] == OK
    bound = repo.parent / "bound-tree"
    _git(repo, "worktree", "add", "-q", str(bound), "-b", "bound")
    # The occupant is the caller itself: a tree another identity ADOPTED is that identity's
    # (B-declared-tree-owner) and a foreign claim makes its own tree instead of refusing.
    assert run_cli(bound, "claim", "T2", agent="lead")[0] == OK

    code, out, err = run_cli(bound, "claim", "T1", agent="lead")
    assert code == REFUSED, out + err
    lease = _item(repo, "T1").lease
    assert lease and lease.holder == "lead", lease

    code, out, err = run_cli(bound, "claim", "T3", agent="lead")
    assert code == REFUSED, out + err
    lease = _item(repo, "T3").lease
    assert not (lease and lease.holder), lease


def test_a_refused_reclaim_at_an_occupied_default_path_keeps_the_held_lease(repo):
    assert run_cli(repo, "init")[0] == OK
    assert run_cli(repo, "task", "add", "T1", "--globs", "a.py")[0] == OK
    assert run_cli(repo, "claim", "T1", "--no-worktree", agent="lead")[0] == OK
    _git(repo, "worktree", "add", "-q", str(repo.parent / ".ddflow-worktrees" / "T1"), "-b", "x")

    code, out, err = run_cli(repo, "claim", "T1", agent="lead")
    assert code == REFUSED, out + err
    lease = _item(repo).lease
    assert lease and lease.holder == "lead", lease


def test_a_detached_recorded_tree_carrying_the_items_branch_is_still_its_tree(repo):
    """Mid-rebase a tree is detached; its work is still the item's."""
    tree = _claimed_with_its_own_tree(repo)
    assert run_cli(repo, "release", "T1", agent="lead")[0] == OK
    _git(tree, "checkout", "-q", "--detach")
    other = repo.parent / "other-tree"
    _git(repo, "worktree", "add", "-q", str(other), "-b", "other-work")
    code, out, err = run_cli(other, "claim", "T1")
    assert code == OK, out + err
    assert _resolved(repo, _item(repo).lease.worktree) == tree.resolve(), out


def _resolved(repo: Path, stored: str) -> Path:
    p = Path(stored)
    return (p if p.is_absolute() else repo / p).resolve()

"""Every path that removes a worktree records it, and onboarding never removes a tree an
agent holds (B5e83fb22cb).

`merge` and the flow log `worktree.removed`; `cleanup --apply` and `onboard` removed
trees without it, so the fold kept `item.worktree` pointing at a directory that was gone.
And onboarding removed with force, outside the log lock and with no look at the leases,
so a tree an agent had just claimed -- clean, nothing ahead of the base -- was deleted.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow import api
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import onboard as ON

OK = 0


def _tree_of(repo: Path, item: str) -> Path:
    code, shown, err = run_cli(repo, "--json", "show", item)
    assert code == OK, err
    tree = Path(json.loads(shown)["worktree"])
    tree = tree if tree.is_absolute() else (repo / tree).resolve()
    assert tree.exists(), "the fixture produced no worktree"
    return tree


def _released_tree(repo: Path) -> Path:
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    code, _, err = run_cli(repo, "claim", "T1", agent="worker")
    assert code == OK, err
    tree = _tree_of(repo, "T1")
    code, _, err = run_cli(repo, "release", "T1", agent="worker")
    assert code == OK, err
    return tree


def _recorded_tree(repo: Path) -> str:
    return fold(EventLog(repo).read_all(), strict=False).items["T1"].worktree


def test_cleanup_apply_records_the_tree_it_removed(repo):
    tree = _released_tree(repo)
    api.cleanup(repo, apply=True, agent="sweeper")
    assert not tree.exists(), "fixture: cleanup should remove a released merged tree"
    assert _recorded_tree(repo) == "", "the fold still points at the removed tree"


def test_onboard_apply_records_the_tree_it_removed(repo):
    tree = _released_tree(repo)
    ON.apply(repo, [str(tree)])
    assert not tree.exists(), "fixture: onboard should remove a released merged tree"
    assert _recorded_tree(repo) == "", "the fold still points at the removed tree"


def test_onboard_apply_never_removes_a_tree_an_agent_holds(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    code, _, err = run_cli(repo, "claim", "T1", agent="worker")
    assert code == OK, err
    tree = _tree_of(repo, "T1")
    out = ON.apply(repo, [str(tree)])
    assert tree.exists(), f"onboard removed a live-leased tree: {out}"
    assert out[0]["outcome"] == "refused" and "worker" in out[0]["detail"], out


def test_onboard_apply_never_removes_a_tree_an_item_adopted(repo):
    """The harness's own working tree, bound to an item and released: no lease protects
    it, only adoption -- as `cleanup` already honours."""
    import subprocess

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    tree = repo.parent / "harness-tree"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-q", str(tree), "-b", "agent-work"],
        check=True,
    )
    code, out_, err = run_cli(tree, "claim", "T1", agent="worker")
    assert code == OK and "adopted" in out_, out_ + err
    assert run_cli(repo, "release", "T1", agent="worker")[0] == OK
    out = ON.apply(repo, [str(tree)])
    assert tree.exists(), f"onboard removed an adopted tree: {out}"


def test_the_removal_is_written_as_the_agent_ddflow_agent_names(repo, monkeypatch):
    tree = _released_tree(repo)
    monkeypatch.setenv("DDFLOW_AGENT", "sweeper-7")
    ON.apply(repo, [str(tree)])
    removed = [e for e in EventLog(repo).read_all() if e.kind == "worktree.removed"]
    assert removed and removed[-1].agent == "sweeper-7", removed

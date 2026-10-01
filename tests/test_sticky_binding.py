"""A wrong worktree binding can be corrected, and merge never lands nothing (Bec8d5228c9).

home-simulator, item 34.8g: an MCP claim bound the item to its parent's harness tree.
The owner worked in its own tree; release + re-claim sent it back to the bound tree
("the item's own tree, from an earlier claim"), `merge --branch <real>` was refused
("it lands that branch ... Drop --branch"), and plain `merge` exited 0 on a branch with
ZERO commits ahead of main -- recording the item merged while its work never landed.

Decision D-sticky-binding-remedy: (A) merge refuses a branch with nothing ahead of its
target (`--allow-empty` for the rare legitimate case); (B) `update <id> --worktree PATH`
rebinds; (D) complete's tree check holds against the rebound tree.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import pass_pipeline, run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.surfaces.mcp import Server

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


def _git(where: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(where), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _item(repo: Path, iid: str = "T1"):
    return fold(EventLog(repo).read_all(), strict=False).items[iid]


def _wrongly_bound(repo: Path) -> tuple[Path, Path]:
    """T1 bound (adopted) to `bridge`, an empty tree; the real work is on `real`."""
    run_cli(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    bridge, real = repo.parent / "bridge", repo.parent / "real"
    _git(repo, "worktree", "add", "-q", str(bridge), "-b", "bridge-work")
    _git(repo, "worktree", "add", "-q", str(real), "-b", "real-work")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    code, out, err = run_cli(bridge, "claim", "T1", agent="impl")
    assert code == OK and _item(repo).branch == "bridge-work", out + err
    (real / "a.py").write_text("a = 1\n")
    _git(real, "add", "a.py")
    _git(real, "commit", "-qm", "the real work")
    return bridge, real


# --- (A) merge refuses to land nothing ------------------------------------------------


def test_merge_refuses_a_branch_with_nothing_ahead_of_its_target(repo):
    _wrongly_bound(repo)
    code, out, err = run_cli(repo, "merge", "T1", "--keep", agent="impl")
    assert code == REFUSED, out + err  # was 0: "merged T1", nothing landed
    assert "bridge-work" in err and "no commits" in err, err
    assert "update T1 --worktree" in err, err
    assert not _item(repo).merged_sha


def test_allow_empty_lands_it_anyway(repo):
    _wrongly_bound(repo)
    code, out, err = run_cli(repo, "merge", "T1", "--keep", "--allow-empty", agent="impl")
    assert code == OK, out + err
    assert _item(repo).merged_sha


# --- (B) update --worktree rebinds --------------------------------------------------


def test_update_worktree_rebinds_the_item_and_the_lease(repo):
    _bridge, real = _wrongly_bound(repo)
    code, out, err = run_cli(repo, "update", "T1", "--worktree", str(real), agent="impl")
    assert code == OK, out + err
    it = _item(repo)
    assert it.branch == "real-work" and "real" in it.worktree, (it.branch, it.worktree)
    assert it.adopted
    assert it.lease is not None and it.lease.branch == "real-work", it.lease
    assert it.lease.worktree == it.worktree
    code, out, err = run_cli(repo, "merge", "T1", "--keep", agent="impl")
    assert code == OK, out + err
    assert _git(repo, "show", "main:a.py") == "a = 1"


def test_a_released_item_rebinds_and_a_reclaim_keeps_the_new_tree(repo):
    _bridge, real = _wrongly_bound(repo)
    assert run_cli(repo, "release", "T1", agent="impl")[0] == OK
    assert run_cli(repo, "update", "T1", "--worktree", str(real), agent="impl")[0] == OK
    code, out, err = run_cli(real, "claim", "T1", agent="impl")
    assert code == OK, out + err
    assert _item(repo).branch == "real-work", out


def test_rebind_refuses_a_tree_bound_to_another_open_item(repo):
    _wrongly_bound(repo)
    run_cli(repo, "task", "add", "T2", "--globs", "b.py")
    other = repo.parent / "other"
    _git(repo, "worktree", "add", "-q", str(other), "-b", "other-work")
    assert run_cli(other, "claim", "T2", agent="impl2")[0] == OK
    code, out, err = run_cli(repo, "update", "T1", "--worktree", str(other), agent="impl")
    assert code == REFUSED, out + err
    assert "T2" in err, err
    assert _item(repo).branch == "bridge-work"


def test_rebind_refuses_what_is_not_a_linked_worktree(repo, tmp_path):
    _wrongly_bound(repo)
    for path in (repo, tmp_path / "nowhere"):
        code, out, err = run_cli(repo, "update", "T1", "--worktree", str(path), agent="impl")
        assert code in (FAIL, REFUSED), out + err
    assert _item(repo).branch == "bridge-work"


def test_rebind_refuses_an_item_another_agent_holds(repo):
    _bridge, real = _wrongly_bound(repo)
    code, out, err = run_cli(repo, "update", "T1", "--worktree", str(real), agent="intruder")
    assert code == REFUSED, out + err
    assert "impl" in err, err
    assert _item(repo).branch == "bridge-work"


def test_mcp_update_takes_a_worktree(repo):
    _bridge, real = _wrongly_bound(repo)
    srv = Server(repo)
    reply = srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "ddflow_update",
                "arguments": {"id": "T1", "worktree": str(real), "as_agent": "impl"},
            },
        }
    )
    assert reply["result"]["_meta"]["exit"] == OK, reply
    assert _item(repo).branch == "real-work"


# --- (D) complete after a rebind ------------------------------------------------------


def test_complete_after_a_rebind_has_no_false_different_tree_note(repo):
    _bridge, real = _wrongly_bound(repo)
    assert run_cli(repo, "update", "T1", "--worktree", str(real), agent="impl")[0] == OK
    pass_pipeline(repo, "T1", omit=("merge",))
    code, out, err = run_cli(repo, "merge", "T1", "--keep", agent="impl")
    assert code == OK, out + err
    sha = _git(repo, "rev-parse", "main")
    code, out, err = run_cli(
        repo, "--json", "complete", "T1", "--sha", sha, "--model", "claude", agent="impl"
    )
    assert code == OK, out + err
    assert "different tree" not in out + err, out + err
    json.loads(out)

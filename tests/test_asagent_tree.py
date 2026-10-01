"""A subagent's work is resolved from its ITEM, not from its parent's tree (B11e4c5a185).

B7c7a0d9222 made `ddflow_claim` stop adopting the connection's harness worktree for a
per-call `as_agent` naming someone else. Two holes were left:

1. Every OTHER tool that reads where the caller stands -- `gate_run`, `merge`, `tests`,
   `review`, `precommit`, `heartbeat`, `gate_record` -- still resolved a subagent's call
   against the PARENT's tree. For an item claimed `--no-worktree` that is not a guess
   about the subagent's work, it is somebody else's: `gate_run` ran the parent's tree
   and recorded the parent's pass as the subagent's, and `merge` landed the parent's
   branch as the subagent's item.
2. `ddflow --agent X claim` from inside a linked worktree adopted it whatever X was. A
   subagent whose shell runs in its parent's harness tree bound its item to the
   parent's tree and branch -- the CLI twin of B7c7a0d9222 (and of home-simulator's
   B5c32cbb5c1).

The tree's OWN identity still adopts it -- no `--agent`, or `--agent` naming that
identity -- and so does a harness-isolated agent naming itself in a tree no other
identity has worked in: that is the adoption the harness feature exists for.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.surfaces.mcp import Server

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3

# Passes only where a.py exists: on the parent's branch, and nowhere else.
PROBE = f"{sys.executable} -c \"import pathlib,sys; sys.exit(0 if pathlib.Path('a.py').exists() else 1)\""


def _git(where: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(where), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _call(srv: Server, name: str, **args) -> dict:
    reply = srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        }
    )
    assert reply is not None
    return reply["result"]


def _item(repo: Path, iid: str):
    return fold(EventLog(repo).read_all(), strict=False).items[iid]


def _parent_tree(repo: Path) -> Path:
    """The parent session's harness worktree, carrying the PARENT's work (a.py)."""
    tree = repo.parent / "parent-tree"
    _git(repo, "worktree", "add", "-q", str(tree), "-b", "parent-work")
    (tree / "a.py").write_text("a = 1\n")
    _git(tree, "add", "a.py")
    _git(tree, "commit", "-qm", "the parent's work")
    return tree


# --- 1. the MCP tools other than claim ------------------------------------------------


def _mcp_setup(repo: Path) -> tuple[Server, Path]:
    """A subagent `sub-1` on its parent's connection, with T1 claimed --no-worktree."""
    run_cli(repo, "init")
    code, out, err = run_cli(repo, "config", "--set", "gate.unit_tests.command", PROBE)
    assert code == OK, out + err
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    tree = _parent_tree(repo)
    run_cli(repo, "task", "add", "T1", "--title", "add b", "--globs", "b.py")
    srv = Server(repo, called_from=tree)
    out = _call(srv, "ddflow_claim", id="T1", no_worktree=True, as_agent="sub-1")
    assert out["_meta"]["exit"] == OK, out
    assert not _item(repo, "T1").worktree, "fixture: T1 must have no tree of its own"
    return srv, tree


def _outcome(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


def test_a_subagents_gate_run_does_not_run_the_parents_tree(repo):
    srv, _tree = _mcp_setup(repo)
    out = _call(srv, "ddflow_gate_run", id="T1", gate="unit_tests", as_agent="sub-1")
    # Was exit 0, "passed": the parent's a.py, recorded as sub-1's change passing.
    assert out["_meta"]["exit"] == NOTHING, out
    gate = _item(repo, "T1").gates.get("unit_tests")
    assert gate is not None and gate.outcome == "unavailable", gate


def test_a_subagents_merge_does_not_land_the_parents_branch(repo):
    srv, _tree = _mcp_setup(repo)
    out = _call(srv, "ddflow_merge", id="T1", as_agent="sub-1")
    # Was exit 0: parent-work merged into main as T1.
    assert out["_meta"]["exit"] == REFUSED, out
    assert _git(repo, "ls-tree", "--name-only", "main", "a.py") == "", "parent's work landed"
    assert not _item(repo, "T1").merged_sha


def test_a_subagents_tests_do_not_diff_the_parents_tree(repo):
    srv, tree = _mcp_setup(repo)
    out = _call(srv, "ddflow_tests", as_agent="sub-1")
    text = json.dumps(out)
    assert str(tree.resolve()) not in text and "parent-tree" not in text, text


def test_the_connections_own_identity_still_works_in_its_tree(repo):
    """Control: with no `as_agent` the caller IS the connection, standing in its tree."""
    srv, _tree = _mcp_setup(repo)
    out = _call(srv, "ddflow_gate_run", id="T1", gate="unit_tests")
    assert out["_meta"]["exit"] == OK, out


# --- 2. `ddflow --agent X claim` from inside someone's tree --------------------------


def _work_there(repo: Path, tree: Path) -> str:
    """Run a ddflow command in `tree` as its own (derived) identity, as the parent session
    does; return that identity, read back from the log rather than re-derived."""
    env = {k: v for k, v in os.environ.items() if k != "DDFLOW_AGENT"}
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    before = {e.id for e in EventLog(repo).read_all()}
    p = subprocess.run(
        [sys.executable, "-m", "ddflow", "task", "add", "PARENT", "--globs", "p.py"],
        cwd=tree,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert p.returncode == OK, p.stdout + p.stderr
    (who,) = {e.agent for e in EventLog(repo).read_all() if e.id not in before}
    return who


def _cli_setup(repo: Path) -> Path:
    run_cli(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    tree = _parent_tree(repo)
    run_cli(repo, "task", "add", "T1", "--globs", "b.py")
    return tree


def test_agent_x_claim_from_another_identitys_tree_makes_its_own(repo):
    """The bug as filed: a subagent's shell in its parent's harness tree."""
    tree = _cli_setup(repo)
    parent = _work_there(repo, tree)
    code, out, err = run_cli(tree, "claim", "T1", agent="sub-1")
    assert code == OK, out + err
    assert f"not adopted: the tree you are in is {parent}'s" in out, out
    t1 = _item(repo, "T1")
    assert not t1.adopted, f"sub-1 adopted the parent's tree: {t1.worktree}"
    assert "parent-tree" not in (t1.worktree or ""), t1.worktree
    assert t1.branch != "parent-work" and (t1.branch or "").endswith("T1"), t1.branch


def test_the_trees_own_identity_named_explicitly_still_adopts(repo):
    tree = _cli_setup(repo)
    who = _work_there(repo, tree)
    code, out, err = run_cli(tree, "claim", "T1", agent=who)
    assert code == OK, out + err
    assert _item(repo, "T1").adopted and _item(repo, "T1").branch == "parent-work"


def test_a_named_agent_alone_in_a_harness_tree_still_adopts_it(repo):
    """Isolation=worktree: nobody else has worked in this tree, so it is the caller's."""
    tree = _cli_setup(repo)
    code, out, err = run_cli(tree, "claim", "T1", agent="worker")
    assert code == OK, out + err
    assert _item(repo, "T1").adopted and _item(repo, "T1").branch == "parent-work"

"""A subagent's claim does not adopt its PARENT's tree (B7c7a0d9222).

Claude Code subagents share their parent session's MCP connection, and name themselves
per call with `as_agent`. The connection's location -- `called_from`, the tree the
server was started in -- is the PARENT's harness worktree. `claim` adopted whatever tree
the caller stood in, so the first subagent's item was bound to the parent's tree and
branch, and the second subagent was refused: "this worktree is already bound to ...".

Where the connection stands is where the connection's OWN identity works. A claim under
a different per-call identity is not standing there: it gets a tree of its own, as the
CLI does from the primary. The connection's own identity, declared or derived, still
adopts -- and an item that already has a tree keeps it, whoever claims it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.surfaces.mcp import Server, _default_agent


def _git(where, *args) -> None:
    subprocess.run(["git", "-C", str(where), *args], check=True, capture_output=True)


def _parent_tree(repo: Path) -> Path:
    """The harness worktree the parent session -- and so its MCP server -- runs in."""
    path = repo.parent / "parent-tree"
    _git(repo, "worktree", "add", "-q", str(path), "-b", "parent-work")
    return path


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


def _setup(repo: Path) -> tuple[Server, Path]:
    run_cli(repo, "init")
    tree = _parent_tree(repo)
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    run_cli(repo, "task", "add", "T2", "--globs", "b.py")
    return Server(repo, called_from=tree), tree


def test_a_subagents_claim_does_not_adopt_the_parents_tree(repo):
    """The bug as filed: two subagents on one connection, each its own item."""
    srv, _tree = _setup(repo)

    first = _call(srv, "ddflow_claim", id="T1", as_agent="sub-1")
    assert first["_meta"]["exit"] == 0, first
    t1 = _item(repo, "T1")
    assert t1.adopted is False, f"sub-1's item adopted the parent's tree {t1.worktree}"
    assert "parent-tree" not in (t1.worktree or ""), t1.worktree
    assert t1.branch != "parent-work", t1.branch
    assert t1.worktree and (t1.branch or "").endswith("T1"), (t1.worktree, t1.branch)

    second = _call(srv, "ddflow_claim", id="T2", as_agent="sub-2")
    assert second["_meta"]["exit"] == 0, second  # was: refused, tree bound to T1
    t2 = _item(repo, "T2")
    assert t2.worktree and t2.worktree != t1.worktree, (t1.worktree, t2.worktree)
    assert t2.lease.holder == "sub-2"


def test_the_connections_own_identity_still_adopts_its_tree(repo):
    """Declared on the connection, and named again per call: that IS the agent whose
    tree it is, so adoption -- the feature for harness-isolated agents -- still holds."""
    srv, _tree = _setup(repo)
    _call(srv, "ddflow_identify", agent="parent")
    out = _call(srv, "ddflow_claim", id="T1", as_agent="parent")
    assert out["_meta"]["exit"] == 0, out
    t1 = _item(repo, "T1")
    assert t1.adopted is True and "parent-tree" in t1.worktree, t1.worktree


def test_as_agent_naming_the_derived_identity_still_adopts(repo):
    """Undeclared, the connection's identity is the derived one; naming it per call
    is the same agent, not a subagent."""
    srv, _tree = _setup(repo)
    who, _src = _default_agent(repo)
    out = _call(srv, "ddflow_claim", id="T1", as_agent=who)
    assert out["_meta"]["exit"] == 0, out
    assert _item(repo, "T1").adopted is True


def test_no_as_agent_still_adopts(repo):
    srv, _tree = _setup(repo)
    out = _call(srv, "ddflow_claim", id="T1")
    assert out["_meta"]["exit"] == 0, out
    assert _item(repo, "T1").adopted is True


def test_an_item_already_bound_to_the_parents_tree_keeps_it(repo):
    """The live bindings this bug already made must not be torn away mid-flight: a
    re-claim by the subagent that holds the item rebinds to its recorded tree."""
    srv, _tree = _setup(repo)
    _call(srv, "ddflow_claim", id="T1")  # bound to the parent's tree, as before the fix
    run_cli(repo, "release", "T1")
    out = _call(srv, "ddflow_claim", id="T1", as_agent="sub-1")
    assert out["_meta"]["exit"] == 0, out
    t1 = _item(repo, "T1")
    assert "parent-tree" in t1.worktree and t1.branch == "parent-work", t1.worktree

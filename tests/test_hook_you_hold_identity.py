"""The commit hook judges a commit in an item's tree by THAT item's lease (Bc57d0b031a, B9929ecb6ea).

Holder equality used to make every lease of the committing identity "mine", whatever tree it
was bound to. An agent holding two items therefore covered tree A's commits with item B's
files: the hook listed B's globs under "You hold" and let the edit through, so a file whose
claim was only QUEUED behind another holder -- or held by this agent's other item -- was
committed from the wrong tree.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from test_hook_lapsed_lease import _setup

from ddflow.infra import worktree as W
from ddflow.infra.log import EventLog
from ddflow.services import enforce as E
from ddflow.services import leases as L


def _second_item(repo: Path, cfg, holder: str, glob: str, *, tree: bool) -> Path | None:
    """T2, held by ``holder`` on ``glob``; in a tree of its own when ``tree``."""
    assert run_cli(repo, "task", "add", "T2", "--globs", glob)[0] == 0
    wt = W.create(repo, cfg, "T2") if tree else None
    L.acquire(
        EventLog(repo, holder),
        cfg,
        "T2",
        holder=holder,
        worktree=W.store_path(repo, wt.path) if wt else "",
        branch=wt.branch if wt else "",
        globs=[glob],
    )
    return wt.path if wt else None


def _stage(tree: Path, rel: str) -> None:
    (tree / rel).parent.mkdir(parents=True, exist_ok=True)
    (tree / rel).write_text("y = 1\n")
    subprocess.run(["git", "-C", str(tree), "add", rel], check=True)


def test_another_items_lease_of_the_same_holder_does_not_cover_this_tree(repo, cfg, monkeypatch):
    tree = _setup(repo, cfg)  # owner holds T1 (src/*) in its own tree
    cfg.enforce.commit_without_lease = "block"
    _second_item(repo, cfg, "owner", "docs/x.md", tree=True)
    _stage(tree, "docs/x.md")
    monkeypatch.chdir(tree)
    code, msg = E.check_commit(repo, cfg, agent="owner")
    assert code == 1, msg
    assert "docs/x.md" in msg and "You hold: src/*" in msg, msg


def test_a_holders_item_without_a_tree_still_covers_its_commit_here(repo, cfg, monkeypatch):
    tree = _setup(repo, cfg)
    cfg.enforce.commit_without_lease = "block"
    _second_item(repo, cfg, "owner", "docs/x.md", tree=False)
    _stage(tree, "docs/x.md")
    monkeypatch.chdir(tree)
    assert E.check_commit(repo, cfg, agent="owner") == (0, "")


def test_a_holder_outside_any_leased_tree_is_covered_by_all_its_leases(repo, cfg, monkeypatch):
    tree = _setup(repo, cfg)
    cfg.enforce.commit_without_lease = "block"
    _stage(repo, "src/other.py")  # the primary checkout is no item's tree
    monkeypatch.chdir(repo)
    assert E.check_commit(repo, cfg, agent="owner") == (0, "")
    assert tree.is_dir()


def test_a_lapsed_trees_commit_is_not_covered_by_the_holders_other_item(repo, cfg, monkeypatch):
    tree = _setup(repo, cfg, lease=False)  # T1's tree exists
    cfg.enforce.commit_without_lease = "block"
    cfg.enforce.lapsed_lease = "refuse"
    EventLog(repo, "owner").append(  # T1's lease, taken long ago and never renewed
        "lease.acquired",
        "T1",
        {
            "holder": "owner",
            "at": time.time() - 10 * cfg.lease.ttl_s,
            "ttl_s": cfg.lease.ttl_s,
            "globs": ["src/*"],
            "worktree": W.store_path(repo, tree),
            "branch": "",
        },
    )
    _second_item(repo, cfg, "owner", "docs/x.md", tree=True)  # live
    subprocess.run(["git", "-C", str(tree), "reset", "-q", "src/new.py"], check=True)
    _stage(tree, "docs/x.md")
    monkeypatch.chdir(tree)
    code, msg = E.check_commit(repo, cfg, agent="owner")
    assert code == 1, msg
    assert "You hold: src/*" not in msg and "docs/x.md" in msg, msg

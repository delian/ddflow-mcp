"""A `no-worktree` tag declares an item takes no tree, so the worktree cap does not hold it (B52).

`--no-worktree` is a flag on `claim`; planning could not tell, so a full tree cap withheld
a review or research task that would have made no tree.
"""

from __future__ import annotations

import json
import subprocess

from conftest import run_cli

import ddflow.api.lifecycle as A


def _setup(repo):
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nmax_parallel = 1\n[schedule]\nmax_parallel_tasks = 4\n"
    )
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "cfg"], check=True)
    run_cli(repo, "task", "add", "BUSY", "--globs", "busy.py")
    run_cli(repo, "task", "add", "CODE", "--globs", "code.py")
    run_cli(repo, "task", "add", "REVIEW", "--globs", "review.py", "--tags", "no-worktree")
    out = A.claim(repo, "BUSY", agent="holder")
    assert out.ok, out.reason


def _ready(repo):
    code, out, err = run_cli(repo, "--json", "next")
    assert code in (0, 2), out + err
    return [r["id"] for r in json.loads(out)["ready"]]


def test_a_full_tree_cap_withholds_code_work_but_not_a_no_worktree_item(repo):
    _setup(repo)
    ready = _ready(repo)
    assert "REVIEW" in ready and "CODE" not in ready, ready


def test_claim_of_a_tagged_item_makes_no_tree(repo):
    _setup(repo)
    out = A.claim(repo, "REVIEW", agent="reviewer")
    assert out.ok, out.reason
    _code, shown, _ = run_cli(repo, "--json", "show", "REVIEW")
    assert not (json.loads(shown).get("worktree") or "")

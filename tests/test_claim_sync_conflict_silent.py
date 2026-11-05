"""B-claim-sync-conflict-silent: `create` must not discard a conflicted base sync.

Claiming an item whose worktree is gone but whose branch exists, with
`worktree.sync_before_start` on (the default), rebuilt the tree at the branch and then
merged base into it. When the branch had diverged from base the merge conflicted, left
MERGE_HEAD set and staged paths, and `create` returned `created` with no warning — so an
agent editing there would commit a merge it never chose.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import run_cli

ME = "agent-sync"


def _git(cwd: Path, *a: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *a], check=True, capture_output=True, text=True
    ).stdout.strip()


def _not_merging(tree: Path) -> bool:
    return (
        subprocess.run(
            ["git", "-C", str(tree), "rev-parse", "-q", "--verify", "MERGE_HEAD"],
            capture_output=True,
        ).returncode
        != 0
    )


def test_create_aborts_a_conflicted_base_sync_and_records_it(repo):
    """The headline: a branch that has diverged from base, claimed again with its tree
    gone, must come back clean-at-the-branch-tip with a warning, not mid-merge."""
    from ddflow import config as C
    from ddflow.infra import worktree as W

    run_cli(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "src/")

    # A branch that exists and has diverged from main on the same file.
    _git(repo, "checkout", "-qb", "ddflow/T1")
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "c.py").write_text("branch\n")
    _git(repo, "add", "src/c.py")
    _git(repo, "commit", "-qm", "branch c")
    _git(repo, "checkout", "-q", "main")
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "c.py").write_text("main\n")
    _git(repo, "add", "src/c.py")
    _git(repo, "commit", "-qm", "main c")

    wt = W.create(repo, C.Config.load(repo), "T1", base="main", branch="ddflow/T1")

    # The conflict was surfaced, not discarded: the note records what went wrong.
    assert wt.sync_note, "a conflicted base sync must be recorded on the tree"
    # And the unfinished merge was aborted, so the tree is clean at the branch tip.
    assert _not_merging(wt.path), "the tree must not be left mid-merge"
    assert _git(wt.path, "status", "--porcelain") == ""

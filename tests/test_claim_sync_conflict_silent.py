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


def _diverged_branch(repo: Path) -> None:
    """A `ddflow/T1` branch that exists and has diverged from main on the same file, so
    merging base into it (what a claim's create-time sync does) conflicts."""
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


def test_create_aborts_a_conflicted_base_sync_and_records_it(repo):
    """A direct `create` of a diverged branch must not leave an unfinished merge
    (regression for the "conflicted" half of B-claim-sync-conflict-silent)."""
    from ddflow import config as C
    from ddflow.infra import worktree as W

    run_cli(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "src/")
    _diverged_branch(repo)

    wt = W.create(repo, C.Config.load(repo), "T1", base="main", branch="ddflow/T1")

    # The conflict was surfaced, not discarded: the note records what went wrong.
    assert wt.sync_note, "a conflicted base sync must be recorded on the tree"
    # And the unfinished merge was aborted, so the tree is clean at the branch tip.
    assert _not_merging(wt.path), "the tree must not be left mid-merge"
    assert _git(wt.path, "status", "--porcelain") == ""


def test_claim_warns_when_the_rebuilt_tree_conflicts(repo):
    """The "reports nothing" half: the `claim` command itself must surface the aborted
    conflicted sync, not answer `created` with no warning."""
    run_cli(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "src/")
    _diverged_branch(repo)

    code, out, err = run_cli(repo, "claim", "T1")

    assert code == 0, err
    assert "base sync conflicted" in (out + err), out + err

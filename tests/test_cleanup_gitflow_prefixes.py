"""`cleanup` recognises gitflow task branches (feature/ bugfix/ hotfix/) as ddflow's (B177).

It knew only `worktree.branch_prefix`, so under gitflow every task branch and worktree
was invisible to it: merged leftovers were never offered for removal.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow import api


def _git(where: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(where), *args], check=True, capture_output=True)


def _gitflow(repo: Path) -> None:
    run_cli(repo, "init")
    with (repo / ".ddflow" / "config.toml").open("a") as fh:
        fh.write('\n[flow]\nmodel = "gitflow"\n')


def _names(out) -> dict[str, str]:
    rows = out.data["trees"] + out.data["stale_branches"]
    return {r["branch"]: r["kind"] for r in rows}


def test_gitflow_branches_and_trees_are_surveyed(repo):
    _git(repo, "branch", "develop")
    _gitflow(repo)
    for br in ("feature/old", "bugfix/old", "hotfix/old", "topic/not-ours"):
        _git(repo, "branch", br)
    tree = repo.parent / "gf-tree"
    _git(repo, "worktree", "add", "-q", str(tree), "-b", "feature/in-tree")

    out = api.cleanup(repo, apply=False, agent="sweeper")

    seen = _names(out)
    assert seen.get("feature/old") == "stale_branch", seen
    assert seen.get("bugfix/old") == "stale_branch", seen
    assert seen.get("hotfix/old") == "stale_branch", seen
    assert "feature/in-tree" in seen, seen
    assert "topic/not-ours" not in seen, "a branch that is nobody's prefix must stay unseen"


def test_gitflow_merged_branch_is_deleted_by_apply(repo):
    _git(repo, "branch", "develop")
    _gitflow(repo)
    _git(repo, "branch", "bugfix/done")
    api.cleanup(repo, apply=True, agent="sweeper")
    refs = subprocess.run(
        ["git", "-C", str(repo), "branch", "--list", "bugfix/done"],
        capture_output=True,
        text=True,
    ).stdout
    assert refs.strip() == "", "a merged bugfix/ branch survived cleanup --apply"


def test_trunk_model_does_not_claim_gitflow_prefixes(repo):
    run_cli(repo, "init")
    _git(repo, "branch", "feature/mine")
    out = api.cleanup(repo, apply=False, agent="sweeper")
    assert "feature/mine" not in _names(out)

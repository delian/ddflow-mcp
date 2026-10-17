"""A `merge` whose git is killed at its timeout AFTER the commit landed is a merge, not a
refusal (B04bab3d1b3).

The post-merge hook runs inside `git merge` and takes minutes under load; the 300 s kill
left the merge gate `failed` for a branch the base held, and a second merge said "no
commits ahead". The merge is now judged by what the base holds.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow import api
from ddflow.infra import git as IG
from ddflow.infra import worktree as W

OK = 0


def _git(where: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(where), *args], check=True, capture_output=True, text=True
    ).stdout


def test_a_timeout_after_the_commit_landed_is_recorded_as_merged(repo, monkeypatch):
    monkeypatch.delenv("DDFLOW_AGENT", raising=False)
    run_cli(repo, "init")
    (repo / "c.txt").write_text("base\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--globs", "c.txt")
    code, out, err = run_cli(repo, "--json", "claim", "T1", agent="alpha")
    assert code == OK, out + err
    tree = Path(json.loads(out)["worktree"])
    (tree / "c.txt").write_text("branch\n")
    _git(tree, "commit", "-qam", "branch edit")

    real = W.merge

    def landed_then_killed(*a, **kw):
        real(*a, **kw)  # the commit lands ...
        return IG.GitResult(
            IG.UNAVAILABLE, "", "git merge timed out after 300s", unavailable=True, timed_out=True
        )  # ... and the hook then outlives the timeout

    monkeypatch.setattr(W, "merge", landed_then_killed)
    out = api.merge_item(repo, "T1", agent="alpha")
    assert out.exit == OK, out
    assert "timed out" in out.data["note"], out.data
    assert _git(repo, "show", "main:c.txt") == "branch\n"
    monkeypatch.undo()
    code, shown, err = run_cli(repo, "--json", "gate", "status", "T1")
    assert code == OK, shown + err
    assert '"failed"' not in shown, shown

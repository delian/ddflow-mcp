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


def _item(repo, monkeypatch) -> Path:
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
    return tree


def _killed(land: bool, monkeypatch) -> None:
    real = W.merge

    def timed_out(*a, **kw):
        if land:
            real(*a, **kw)  # the commit lands ...
        return IG.GitResult(
            IG.UNAVAILABLE, "", "git merge timed out after 300s", unavailable=True, timed_out=True
        )  # ... and the hook then outlives the timeout

    monkeypatch.setattr(W, "merge", timed_out)


def _done(repo) -> list[str]:
    code, shown, err = run_cli(repo, "--json", "gate", "status", "T1")
    assert code == OK, shown + err
    return json.loads(shown)["done"]


def test_a_timeout_after_the_commit_landed_is_recorded_as_merged(repo, monkeypatch):
    _item(repo, monkeypatch)
    _killed(True, monkeypatch)
    out = api.merge_item(repo, "T1", agent="alpha")
    assert out.exit == OK, out
    assert "timed out" in out.data["kept_reason"], out.data
    assert _git(repo, "show", "main:c.txt") == "branch\n"
    monkeypatch.undo()
    assert "merge" in _done(repo)


def test_a_timeout_before_anything_landed_is_still_a_failure(repo, monkeypatch):
    _item(repo, monkeypatch)
    _killed(False, monkeypatch)
    out = api.merge_item(repo, "T1", agent="alpha")
    assert out.exit != OK, out
    assert _git(repo, "show", "main:c.txt") == "base\n"
    monkeypatch.undo()
    assert "merge" not in _done(repo)


def test_the_branch_ref_behind_its_trees_head_is_not_taken_for_landed(repo, monkeypatch):
    """The base moved while git was killed and the branch REF is contained in it, but the
    commit this run tried to land (the tree's head) is not: not a landing."""
    import importlib

    M = importlib.import_module("ddflow.api.lifecycle.merge")
    tree = _item(repo, monkeypatch)
    head = _git(tree, "rev-parse", "HEAD").strip()
    _git(tree, "checkout", "-q", "--detach", head)
    before = _git(repo, "rev-parse", "main").strip()
    _git(repo, "branch", "-f", "ddflow/T1", "main")  # the ref lags the tree's head
    (repo / "d.txt").write_text("other\n")
    _git(repo, "add", "d.txt")
    _git(repo, "commit", "-qm", "someone else landed")
    wt = W.Worktree(item="T1", path=tree, branch="ddflow/T1", base="main")
    r = IG.GitResult(IG.UNAVAILABLE, "", "timed out", unavailable=True, timed_out=True)
    assert M._landed_anyway(repo, wt, before, head, r) == ""
    assert M._landed_anyway(repo, wt, before, _git(repo, "rev-parse", "HEAD").strip(), r)

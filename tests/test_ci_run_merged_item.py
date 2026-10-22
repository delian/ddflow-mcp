"""`ddflow ci run <item-id>` after the merge (B-ci-run-item-id-branch-gone).

The id mapped to the item's branch, which `ddflow merge` deletes: the run then said the ref
was not a commit. A merged item is checked at the commit it landed as."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import run_cli
from helpers import commit as _commit
from helpers import git as _git
from test_ci_gate import _item_with_ci

from ddflow.api import cirun as A


def _merged_item(repo, monkeypatch):
    _item_with_ci(repo, monkeypatch)
    _, out, _ = run_cli(repo, "--json", "show", "T1")
    shown = json.loads(out)
    tree = Path(shown["worktree"])
    tree = tree if tree.is_absolute() else repo / tree
    _commit(tree, {"src/x.py": "x\n"}, "work")
    code, _o, err = run_cli(repo, "gate", "record", "T1", "implement", "--outcome", "passed",
                            "--evidence", "test")  # fmt: skip
    assert code == 0, err
    code, _o, err = run_cli(repo, "gate", "skip", "T1", "ci", "--reason", "the test merges")
    assert code == 0, err
    code, out, err = run_cli(repo, "merge", "T1")
    assert code == 0, out + err
    return shown["branch"]


def test_a_merged_items_id_checks_the_commit_it_landed_as(repo, monkeypatch):
    branch = _merged_item(repo, monkeypatch)
    assert not _git(repo, "branch", "--list", branch).strip(), "the merge keeps no branch"
    res = A.run(repo, ref="T1")
    assert "is not a commit" not in (res.reason or ""), res.reason
    assert res.exit == 0, (res.reason, res.data)
    _, out, _ = run_cli(repo, "--json", "show", "T1")
    assert res.data["sha"] == json.loads(out)["merged_sha"]


def test_an_unmerged_item_without_a_branch_says_so(repo, monkeypatch):
    _item_with_ci(repo, monkeypatch)
    _, out, _ = run_cli(repo, "--json", "show", "T1")
    shown = json.loads(out)
    _git(repo, "worktree", "remove", "--force", shown["worktree"])
    _git(repo, "branch", "-D", shown["branch"])
    res = A.run(repo, ref="T1")
    assert res.exit != 0 and "branch" in res.reason and "T1" in res.reason, res.reason
    assert "is not a commit" not in res.reason


def test_what_landed_outranks_a_branch_left_behind_and_a_missing_commit_is_said(repo):
    from types import SimpleNamespace as NS

    head = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "branch", "left-behind")
    assert A._item_ref(repo, NS(id="T", branch="left-behind", merged_sha=head)) == (head, "")
    ref, why = A._item_ref(repo, NS(id="T", branch="", merged_sha="f" * 40))
    assert ref == "" and "which this clone does not have" in why

"""A gitflow hotfix's back-merge request is tracked until it lands (B171).

`pr sync` opened the hotfix -> develop request and reported it once; the item completed as
soon as production had the fix and nothing ever re-checked that develop got it, so a
back-merge closed unmerged re-broke production at the next release.
"""

# ruff: noqa: F811  (fixtures imported from test_flow)
from __future__ import annotations

import json
from pathlib import Path

from conftest import pass_pipeline, run_cli
from test_flow import AUTHOR, _commit, _git, _state, _work, gitflow_pr, pr_repo  # noqa: F401


def _file_hotfix(repo: Path) -> None:
    run_cli(repo, "task", "add", "H1", "--globs", "fix.py", "--tags", "hotfix")
    code, out, err = run_cli(repo, "--json", "claim", "H1")
    assert code == 0, err
    tree = Path(json.loads(out)["worktree"])
    _commit(tree, "fix.py", "fixed\n", "fix: outage")
    pass_pipeline(repo, "H1", omit=("merge",))
    code, _out, err = run_cli(repo, "merge", "H1", "--model", AUTHOR)
    assert code == 0, err


def _to_back_merge(gitflow_pr):
    repo, forge, _remote = gitflow_pr
    _file_hotfix(repo)
    forge.merge_as_human(1)
    code, out, err = run_cli(repo, "--json", "pr", "sync")
    assert code == 0, err
    return repo, forge, json.loads(out)


def test_the_back_merge_request_is_recorded_and_shown_by_pr_status(gitflow_pr):
    repo, _forge, synced = _to_back_merge(gitflow_pr)
    assert any(c["what"] == "back_merge" and "opened" in c["detail"] for c in synced["changes"])
    assert _state(repo).items["H1"].state == "done", "the item completes once production has it"
    _, out, _ = run_cli(repo, "--json", "pr", "status")
    rows = json.loads(out)["back_merges"]
    assert [(r["item"], r["into"], r["state"]) for r in rows] == [("H1", "develop", "open")], rows


def test_a_later_sync_reports_the_back_merge_landing(gitflow_pr):
    repo, forge, _ = _to_back_merge(gitflow_pr)
    forge.merge_as_human(2)
    code, out, err = run_cli(repo, "--json", "pr", "sync")
    assert code == 0, err
    changes = json.loads(out)["changes"]
    assert any(
        c["item"] == "H1" and c["what"] == "back_merge" and "merged" in c["detail"] for c in changes
    ), changes
    rows = json.loads(run_cli(repo, "--json", "pr", "status")[1])["back_merges"]
    assert rows[0]["state"] == "merged"
    # Settled: the next sync does not ask about it again.
    before = len(forge.calls("pr", "view"))
    run_cli(repo, "--json", "pr", "sync")
    assert len(forge.calls("pr", "view")) == before


def test_a_back_merge_closed_unmerged_is_refused_loudly(gitflow_pr):
    repo, forge, _ = _to_back_merge(gitflow_pr)
    forge.edit(2, state="CLOSED")
    _, out, _ = run_cli(repo, "--json", "pr", "sync")
    body = json.loads(out)
    assert any("H1" in r and "develop" in r and "closed" in r for r in body["refused"]), body
    rows = json.loads(run_cli(repo, "--json", "pr", "status")[1])["back_merges"]
    assert rows[0]["state"] == "closed"

"""Bug B31c0109012: `gate record implement --outcome passed` needs a commit on the branch."""

from __future__ import annotations

import subprocess

from conftest import run_cli

from ddflow.core.model import fold
from ddflow.infra import worktree as W
from ddflow.infra.log import EventLog


def _claimed(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "src/**")
    code, out, err = run_cli(repo, "claim", "T1", agent="a1")
    assert code == 0, out + err
    wt = fold(EventLog(repo).read_all()).items["T1"].worktree
    return W.load_path(repo, wt)


def _implement(repo, *extra):
    return run_cli(
        repo,
        "gate",
        "record",
        "T1",
        "implement",
        "--outcome",
        "passed",
        "--evidence",
        "x",
        *extra,
        agent="a1",
    )


def _outcome(repo):
    g = fold(EventLog(repo).read_all()).items["T1"].gates.get("implement")
    return g.outcome if g else ""


def test_implement_passed_over_an_empty_branch_is_refused(repo):
    _claimed(repo)
    code, out, err = _implement(repo)
    assert code == 3 and "no commit ahead" in out + err, (code, out, err)
    assert _outcome(repo) == ""


def test_implement_passed_after_a_commit_is_recorded(repo):
    tree = _claimed(repo)
    (tree / "src").mkdir()
    (tree / "src" / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(tree), "add", "src"], check=True)
    subprocess.run(["git", "-C", str(tree), "commit", "-qm", "work"], check=True)
    code, out, err = _implement(repo)
    assert code == 0, out + err
    assert _outcome(repo) == "passed"


def test_force_records_implement_over_an_empty_branch(repo):
    _claimed(repo)
    code, out, err = _implement(repo, "--force", "--reason", "changes nothing")
    assert code == 0, out + err

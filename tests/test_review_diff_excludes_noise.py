"""B-delta-review-output-mixed-with-other-agents: a review diff carried other agents' event
shards and a gitlink (a nested repository `git add`ed by accident). The first are ddflow's
bookkeeping (what reached a branch through the base's own commits), the second is a commit id
of something that is not this repository's code; neither is the item's work to be judged."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ddflow.api import review as RV


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


def _branch_with_noise(tmp_path: Path) -> tuple[Path, str, str]:
    r = tmp_path / "r"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@e.com")
    git(r, "config", "user.name", "T")
    (r / "a.py").write_text("a = 1\n")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "base")
    head = git(r, "rev-parse", "HEAD")
    git(r, "checkout", "-q", "-b", "work")
    (r / "a.py").write_text("a = 2\n")
    (r / ".ddflow" / "events").mkdir(parents=True)
    (r / ".ddflow" / "events" / "other-agent.jsonl").write_text('{"kind": "ci.recorded"}\n')
    git(r, "add", "a.py", ".ddflow")
    git(r, "update-index", "--add", "--cacheinfo", f"160000,{head},tmp.cDDAI7SLqb")
    git(r, "commit", "-qm", "work")
    assert "160000" in git(r, "ls-tree", "work", "tmp.cDDAI7SLqb"), "the gitlink is committed"
    return r, head, "work"


def test_a_delta_of_a_branch_leaves_out_event_shards_and_gitlinks(tmp_path):
    r, head, tip = _branch_with_noise(tmp_path)
    diff = RV._delta_of_branch(r, tip, head, "main")
    assert "a.py" in diff and "a = 2" in diff
    assert ".ddflow/events" not in diff and "Subproject commit" not in diff, diff


def test_the_diff_of_an_items_tree_leaves_them_out_too(tmp_path):
    r, _head, _tip = _branch_with_noise(tmp_path)
    diff, _how = RV._tree_diff(r, "main")
    assert "a = 2" in diff
    assert ".ddflow/events" not in diff and "Subproject commit" not in diff, diff


def test_a_registered_submodules_pointer_change_is_still_reviewed(tmp_path):
    r, head, tip = _branch_with_noise(tmp_path)
    (r / ".gitmodules").write_text('[submodule "lib"]\n\tpath = lib\n\turl = ../lib\n')
    git(r, "add", ".gitmodules")
    git(r, "update-index", "--add", "--cacheinfo", f"160000,{head},lib")
    git(r, "commit", "-qm", "register lib")
    diff = RV._delta_of_branch(r, tip, head, "main")
    assert "diff --git a/lib b/lib" in diff and f"+Subproject commit {head}" in diff, diff
    assert "tmp.cDDAI7SLqb" not in diff, diff

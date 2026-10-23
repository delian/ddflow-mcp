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


def test_a_submodule_whose_name_differs_from_its_path_is_registered_by_its_path(tmp_path):
    r, head, tip = _branch_with_noise(tmp_path)
    (r / ".gitmodules").write_text(
        '[submodule "vendor-lib"]\n\tpath = vendor/lib\n\turl = ../lib\n'
    )
    git(r, "add", ".gitmodules")
    git(r, "update-index", "--add", "--cacheinfo", f"160000,{head},vendor/lib")
    git(r, "commit", "-qm", "register vendor/lib")
    diff = RV._delta_of_branch(r, tip, head, "main")
    assert "diff --git a/vendor/lib b/vendor/lib" in diff and "tmp.cDDAI7SLqb" not in diff, diff


def test_a_submodule_the_branch_deletes_is_still_reviewed(tmp_path):
    r, head, _tip = _branch_with_noise(tmp_path)
    (r / ".gitmodules").write_text('[submodule "lib"]\n\tpath = lib\n\turl = ../lib\n')
    git(r, "add", ".gitmodules")
    git(r, "update-index", "--add", "--cacheinfo", f"160000,{head},lib")
    git(r, "commit", "-qm", "register lib")
    git(r, "checkout", "-q", "-b", "work2")
    git(r, "rm", "-q", "--cached", "lib")
    (r / ".gitmodules").write_text("")
    git(r, "add", ".gitmodules")
    git(r, "commit", "-qm", "drop lib")
    diff, _how = RV._tree_diff(r, "work")
    assert "deleted file mode 160000" in diff and "a/lib" in diff, diff


def test_a_file_replaced_by_an_unregistered_gitlink_is_dropped(tmp_path):
    from ddflow.services.review_noise import without_stray_gitlinks

    diff = (
        "diff --git a/x b/x\nold mode 100644\nnew mode 160000\nindex 1..2\n"
        "-Subproject commit a\n+Subproject commit b\n"
        "diff --git a/ok.py b/ok.py\nindex 1..2 100644\n+x = 1\n"
    )
    out = without_stray_gitlinks(diff, set())
    assert "a/x" not in out and "ok.py" in out, out


def test_a_path_that_is_not_plain_is_kept_not_guessed_at():
    from ddflow.services.review_noise import without_stray_gitlinks

    for header in (
        'diff --git "a/caf\\303\\251" "b/caf\\303\\251"',  # git C-quoted it
        "diff --git a/lib b/foo b/lib b/foo",  # a path holding ' b/': the header is ambiguous
        "diff --git a/lib # local b/lib # local",
    ):
        diff = f"{header}\nnew file mode 160000\n+Subproject commit b\n"
        assert without_stray_gitlinks(diff, set()) == diff, header


def test_git_reads_the_registered_paths_quotes_and_comments_included(tmp_path):
    from ddflow.services.review_noise import registered_submodules

    r, _head, _tip = _branch_with_noise(tmp_path)
    (r / ".gitmodules").write_text(
        '[submodule "a"]\n\tpath = lib # local copy\n[submodule "b"]\n\tpath = "x # y"\n'
    )
    assert registered_submodules(r) == {"lib", "x # y"}


def test_a_single_commit_review_leaves_the_noise_out_too(tmp_path):
    r, _head, tip = _branch_with_noise(tmp_path)
    diff, _how = RV.commit_diff(r, git(r, "rev-parse", tip))
    assert "a = 2" in diff
    assert ".ddflow/events" not in diff and "tmp.cDDAI7SLqb" not in diff, diff

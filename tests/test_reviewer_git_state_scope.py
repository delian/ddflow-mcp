"""The command reviewer's git guard watches the tree under review (bug B708761957b).

It used to snapshot the process cwd, so a sibling worktree's commit or stash (the stash
list is shared by every worktree) made a review of an unrelated tree `unavailable`.
"""

from __future__ import annotations

from pathlib import Path

from helpers import git_raw as _git

from ddflow.services import review as R
from ddflow.services.gates.reviewers import git_state


def _repo(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "t")
    (path / "f.txt").write_text("one\n")
    _git(path, "add", "f.txt")
    _git(path, "commit", "-qm", "init")
    return path


def _with_sibling(tmp_path: Path) -> tuple[Path, Path]:
    primary = _repo(tmp_path / "primary")
    wt = tmp_path / "wt"
    _git(primary, "worktree", "add", "-q", "-b", "lane", str(wt))
    return primary, wt


def _rev(command: str) -> R.Reviewer:
    return R.Reviewer(name="r", kind="command", command=command)


def test_a_sibling_stash_and_commit_during_a_review_do_not_make_it_unavailable(
    tmp_path, monkeypatch
):
    primary, wt = _with_sibling(tmp_path)
    monkeypatch.chdir(wt)
    sibling = (
        f"cd {primary} && echo x >> f.txt && git stash push -q -m sib && "
        f"echo y >> f.txt && git commit -qam sib; echo LGTM"
    )
    out, err = R._chat(_rev(sibling), "sys", "user", 30, tree=wt)
    assert (out, err) == ("LGTM", ""), err


def test_git_state_ignores_the_shared_stash_list(tmp_path):
    primary, wt = _with_sibling(tmp_path)
    before = git_state(wt)
    (primary / "f.txt").write_text("sibling wip\n")
    _git(primary, "stash", "push", "-q", "-m", "sibling")
    assert git_state(wt) == before


def test_activity_in_the_cwd_checkout_is_not_the_reviewed_trees(tmp_path, monkeypatch):
    elsewhere = _repo(tmp_path / "elsewhere")
    tree = _repo(tmp_path / "tree")
    monkeypatch.chdir(elsewhere)
    cmd = f"echo z >> {elsewhere}/f.txt; git -C {elsewhere} commit -qam moved; echo LGTM"
    out, err = R._chat(_rev(cmd), "sys", "user", 30, tree=tree)
    assert (out, err) == ("LGTM", ""), err


def test_the_tool_runs_in_the_tree_it_is_watched_in(tmp_path, monkeypatch):
    elsewhere = _repo(tmp_path / "elsewhere")
    tree = _repo(tmp_path / "tree")
    monkeypatch.chdir(elsewhere)
    out, err = R._chat(_rev("pwd"), "sys", "user", 30, tree=tree)
    assert Path(out).resolve() == tree.resolve(), err


def test_a_reviewer_that_edits_the_reviewed_tree_still_did_not_review(tmp_path, monkeypatch):
    primary, wt = _with_sibling(tmp_path)
    monkeypatch.chdir(primary)
    out, err = R._chat(_rev(f"echo edit >> {wt}/f.txt; echo LGTM"), "sys", "user", 30, tree=wt)
    assert out == "" and "git state" in err


def test_a_reviewer_that_commits_in_the_reviewed_tree_still_did_not_review(tmp_path, monkeypatch):
    primary, wt = _with_sibling(tmp_path)
    monkeypatch.chdir(wt)
    cmd = "echo c >> f.txt; git commit -qam tool; echo LGTM"
    out, err = R._chat(_rev(cmd), "sys", "user", 30, tree=wt)
    assert out == "" and "git state" in err

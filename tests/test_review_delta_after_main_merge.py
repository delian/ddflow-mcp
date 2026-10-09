"""Bccf6d1aec7: a delta review after `git merge main` sent main's incoming commits too.

The delta diffed from the last reviewed head to the branch tip, a range that holds every
commit the merge brought in from main -- 560-695k characters of other items' code,
reviewed elsewhere already. The delta is the item's OWN change since the reviewed head:
main's incoming work is taken as given (the reviewed head merged with what came in),
and only what the branch did on top of that is sent.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from ddflow.api import review as RV


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(cwd: Path, path: str, text: str, msg: str) -> str:
    (cwd / path).parent.mkdir(parents=True, exist_ok=True)
    (cwd / path).write_text(text)
    _git(cwd, "add", path)
    _git(cwd, "commit", "-qm", msg)
    return _git(cwd, "rev-parse", "HEAD")


@pytest.fixture
def merged(repo: Path, tmp_path: Path):
    """main, an item branch in its own worktree reviewed at `head`, then main moves on
    (a large unrelated change), the item merges main, and commits one more change."""
    tree = tmp_path / "item"
    _git(repo, "worktree", "add", "-q", "-b", "item", str(tree))
    head = _commit(tree, "own.py", "x = 1\n", "item: first")
    _commit(repo, "theirs.py", "".join(f"y{i} = {i}\n" for i in range(200)), "main: other item")
    _git(tree, "merge", "-q", "--no-edit", "main")
    _commit(tree, "own.py", "x = 2\n", "item: after review")
    return repo, tree, head


def _item(tree: Path, branch: str = "item"):
    return SimpleNamespace(id="T1", worktree=str(tree), branch=branch)


def test_the_delta_from_the_worktree_is_the_items_own_change(merged):
    repo, tree, head = merged
    diff = RV._delta_diff(repo, _item(tree), "", head)
    assert "theirs.py" not in diff, "main's incoming commit was sent to the reviewer"
    assert "own.py" in diff and "+x = 2" in diff


def test_the_delta_of_a_named_branch_is_the_items_own_change(merged):
    repo, _tree, head = merged
    diff = RV._delta_diff(repo, _item(Path("/nonexistent")), "item", head)
    assert "theirs.py" not in diff
    assert "+x = 2" in diff


def test_the_delta_counts_only_the_items_own_commits(merged):
    repo, tree, head = merged
    assert RV._commits_since(repo, _item(tree), "", head) == 2  # its commit + the merge


def test_a_change_made_while_resolving_the_merge_is_sent(merged, tmp_path):
    """What the item does to main's file after merging it is its own change; the rest of
    main's file is not."""
    repo, tree, head = merged
    (tree / "theirs.py").write_text("y0 = 'edited by the item'\n")
    _git(tree, "commit", "-qam", "item: touch theirs")
    diff = RV._delta_diff(repo, _item(tree), "", head)
    assert "edited by the item" in diff
    assert "+y199 = 199" not in diff  # the rest of main's file is not the item's


def test_without_a_main_merge_the_delta_is_unchanged(repo, tmp_path):
    tree = tmp_path / "item"
    _git(repo, "worktree", "add", "-q", "-b", "item", str(tree))
    head = _commit(tree, "own.py", "x = 1\n", "first")
    _commit(tree, "own.py", "x = 2\n", "second")
    diff = RV._delta_diff(repo, _item(tree), "", head)
    assert "+x = 2" in diff and "-x = 1" in diff


def test_a_conflicted_merge_still_sends_none_of_mains_other_work(repo, tmp_path):
    """Reviewed head and main both changed one file; the item resolved the conflict. The
    baseline keeps main's side of that conflict, so the delta is where the resolution
    departs from it -- and none of main's unrelated work."""
    tree = tmp_path / "item"
    _commit(repo, "shared.py", "v = 0\n", "base")
    _git(repo, "worktree", "add", "-q", "-b", "item", str(tree))
    head = _commit(tree, "shared.py", "v = 'item'\n", "item: change shared")
    _commit(repo, "shared.py", "v = 'main'\n", "main: change shared")
    _commit(repo, "theirs.py", "unrelated = 1\n", "main: other item")
    subprocess.run(["git", "-C", str(tree), "merge", "-q", "main"], capture_output=True)
    (tree / "shared.py").write_text("v = 'resolved'\n")
    _git(tree, "commit", "-qam", "item: resolve")
    diff = RV._delta_diff(repo, _item(tree), "", head)
    assert "theirs.py" not in diff
    # exactly where the resolution departs from main's side
    assert "+v = 'resolved'" in diff and "-v = 'main'" in diff
    assert "v = 'item'" not in diff


def test_a_commit_made_before_the_merge_is_still_sent(repo, tmp_path):
    """Reviewed at H; the item commits B; THEN merges main. The start is H merged with
    main's work (a tree), not the merge commit, so B is in the delta."""
    tree = tmp_path / "item"
    _git(repo, "worktree", "add", "-q", "-b", "item", str(tree))
    head = _commit(tree, "own.py", "x = 1\n", "item: reviewed")
    _commit(tree, "before.py", "made_before_the_merge = True\n", "item: B")
    _commit(repo, "theirs.py", "unrelated = 1\n", "main: other item")
    _git(tree, "merge", "-q", "--no-edit", "main")
    for diff in (
        RV._delta_diff(repo, _item(tree), "", head),
        RV._delta_diff(repo, _item(Path("/nonexistent")), "item", head),
    ):
        assert "made_before_the_merge" in diff
        assert "theirs.py" not in diff


def test_a_resolution_that_takes_mains_side_sends_nothing_of_main(repo, tmp_path):
    tree = tmp_path / "item"
    _commit(repo, "shared.py", "v = 0\n", "base")
    _git(repo, "worktree", "add", "-q", "-b", "item", str(tree))
    head = _commit(tree, "shared.py", "v = 'item'\n", "item: change shared")
    _commit(repo, "shared.py", "v = 'main'\n", "main: change shared")
    subprocess.run(["git", "-C", str(tree), "merge", "-q", "main"], capture_output=True)
    (tree / "shared.py").write_text("v = 'main'\n")
    _git(tree, "commit", "-qam", "item: take main's side")
    _commit(tree, "own.py", "x = 1\n", "item: more")
    diff = RV._delta_diff(repo, _item(tree), "", head)
    assert "shared.py" not in diff and "+x = 1" in diff


def test_a_criss_cross_history_still_excludes_mains_work(repo, tmp_path):
    """main merged the item's reviewed head, and the item merged main: two merge bases.
    The one the reviewed head already holds is not 'incoming'."""
    tree = tmp_path / "item"
    _git(repo, "worktree", "add", "-q", "-b", "item", str(tree))
    head = _commit(tree, "own.py", "x = 1\n", "item: reviewed")
    _commit(repo, "theirs.py", "".join(f"y{i} = {i}\n" for i in range(50)), "main: other")
    _git(tree, "merge", "-q", "--no-edit", "main")
    _git(repo, "merge", "-q", "--no-edit", head)  # main takes the item's reviewed head
    _commit(repo, "later.py", "z = 1\n", "main: later")
    _git(tree, "merge", "-q", "--no-edit", "main")
    _commit(tree, "own.py", "x = 2\n", "item: after")
    diff = RV._delta_diff(repo, _item(tree), "", head)
    assert "theirs.py" not in diff and "later.py" not in diff
    assert "+x = 2" in diff


def test_two_incoming_merge_bases_are_both_merged_onto_the_head(repo, tmp_path):
    """A true criss-cross: main merged the item's I1 while the item merged main's M1, so
    `merge-base --all` names both, and the reviewed head H holds neither."""
    tree = tmp_path / "item"
    _git(repo, "worktree", "add", "-q", "-b", "item", str(tree))
    head = _commit(tree, "own.py", "x = 1\n", "item: H, reviewed")
    i1 = _commit(tree, "i1.py", "i = 1\n", "item: I1")
    m1 = _commit(repo, "theirs.py", "m = 1\n", "main: M1")
    _git(repo, "merge", "-q", "--no-edit", i1)
    _git(tree, "merge", "-q", "--no-edit", m1)
    bases = _git(repo, "merge-base", "--all", "main", "item").split()
    assert sorted(bases) == sorted([i1, m1])
    _commit(tree, "own.py", "x = 2\n", "item: after")
    diff = RV._delta_diff(repo, _item(tree), "", head)
    assert "theirs.py" not in diff and "+x = 2" in diff


def test_a_diff_that_cannot_run_is_never_nothing_changed(merged, monkeypatch):
    """The branch path: a diff from the merged start that fails is refused with the
    reason -- never reported as 'nothing changed', and never retried over the wider
    since-head range, which holds main's work."""
    repo, _tree, head = merged
    real = RV.W.git

    def failing(where, *args, **kw):  # only the merged-start diff fails; since-head works
        if args[:1] == ("diff",) and f"{head}...item" not in args:
            return RV.W.GitResult(1, "", "boom")
        return real(where, *args, **kw)

    monkeypatch.setattr(RV.W, "git", failing)
    with pytest.raises(RuntimeError, match="boom"):  # a retry over head...item would succeed
        RV._delta_diff(repo, _item(Path("/nonexistent")), "item", head)
    it = SimpleNamespace(id="T1", worktree="", branch="item")

    monkeypatch.setattr(RV, "_last_head", lambda *a, **k: head)
    diff, _how, why = RV._delta_scope(repo, it, "critic", "item", "main")
    assert diff == "" and "could not be produced" in why and "boom" in why


def test_the_worktree_delta_that_cannot_run_is_refused_not_empty(merged, monkeypatch):
    repo, tree, head = merged
    real = RV.W.git

    def failing(where, *args, **kw):
        if args[:1] == ("diff",):
            return RV.W.GitResult(1, "", "simulated failure")
        return real(where, *args, **kw)

    monkeypatch.setattr(RV.W, "git", failing)
    with pytest.raises(RuntimeError, match="simulated failure"):
        RV._delta_diff(repo, _item(tree), "", head)


def test_a_merge_that_cannot_be_written_still_sends_none_of_mains_work(repo, tmp_path):
    """Main deleted a file the reviewed head changed (modify/delete: `-X theirs` cannot
    settle it). The delta falls back to the merged-in main commit: the item's own change
    on top of main, without main's other work."""
    tree = tmp_path / "item"
    _commit(repo, "gone.py", "g = 0\n", "base")
    _git(repo, "worktree", "add", "-q", "-b", "item", str(tree))
    head = _commit(tree, "gone.py", "g = 'item'\n", "item: change gone.py")
    _git(repo, "rm", "-q", "gone.py")
    _git(repo, "commit", "-qm", "main: delete gone.py")
    _commit(repo, "theirs.py", "unrelated = 1\n", "main: other item")
    merged = subprocess.run(["git", "-C", str(tree), "merge", "-q", "main"], capture_output=True)
    assert merged.returncode != 0 and (tree / "theirs.py").exists()  # the conflict, main's work in
    _git(tree, "rm", "-q", "gone.py")
    _git(tree, "commit", "-qm", "item: accept the deletion")
    _commit(tree, "own.py", "x = 1\n", "item: more")
    diff = RV._delta_diff(repo, _item(tree), "", head)
    assert "theirs.py" not in diff and "+x = 1" in diff


def test_a_worktree_delta_with_nothing_merged_that_cannot_run_is_refused(
    repo, tmp_path, monkeypatch
):
    tree = tmp_path / "item"
    _git(repo, "worktree", "add", "-q", "-b", "item", str(tree))
    head = _commit(tree, "own.py", "x = 1\n", "first")
    _commit(tree, "own.py", "x = 2\n", "second")
    real = RV.W.git
    monkeypatch.setattr(
        RV.W, "git",
        lambda where, *a, **k: RV.W.GitResult(1, "", "boom") if a[:1] == ("diff",) else real(where, *a, **k),
    )  # fmt: skip
    with pytest.raises(RuntimeError, match="boom"):
        RV._delta_diff(repo, _item(tree), "", head)

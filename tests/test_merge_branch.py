"""`merge` for an item claimed WITHOUT a worktree: it lands the branch the work is on.

`claim --no-worktree` is the documented way past a tree that is already bound to another
item, and the only way to work an item from a tree ddflow will not bind. `merge` then
refused ("has no worktree to merge"), so the work was landed by hand -- outside the log,
with no `worktree.merged` event and no merge gate -- the first time it was used for real
(B-prepush-isolated, 2026-09-30).

With no tree of its own, the item's branch is the one named with `--branch`, else the
branch checked out in the linked worktree the caller stands in. Standing in the primary
names nothing, and a guess there would land whatever the primary happens to be on.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git as _git

from ddflow.core.model import fold
from ddflow.infra.log import EventLog

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


def _setup(repo: Path) -> Path:
    """An item claimed --no-worktree, worked in the harness's own tree on `agent-work`."""
    run_cli(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    tree = repo.parent / "agent-tree"
    _git(repo, "worktree", "add", "-q", str(tree), "-b", "agent-work")
    run_cli(repo, "task", "add", "T1", "--title", "add a", "--globs", "a.py")
    code, out, err = run_cli(tree, "claim", "T1", "--no-worktree")
    assert code == OK, out + err
    return tree


def _commit(tree: Path, name: str, text: str = "x = 1\n") -> None:
    (tree / name).write_text(text)
    _git(tree, "add", name)
    _git(tree, "commit", "-qm", f"add {name}")


def _merged_events(repo: Path) -> list[dict]:
    return [e.data for e in EventLog(repo).read_all() if e.kind == "worktree.merged"]


def test_it_lands_the_branch_of_the_tree_the_caller_stands_in(repo):
    tree = _setup(repo)
    _commit(tree, "a.py")
    code, out, err = run_cli(tree, "merge", "T1")
    assert code == OK, out + err
    assert _git(repo, "show", "main:a.py") == "x = 1"
    (ev,) = _merged_events(repo)
    assert ev["branch"] == "agent-work"
    # The gate the merge is supposed to record, recorded.
    it = fold(EventLog(repo).read_all(), strict=False).items["T1"]
    assert it.merged_sha == _git(repo, "rev-parse", "main")  # the landing, not the branch head
    # And the tree it borrowed is the caller's, so it is left exactly where it was.
    assert tree.is_dir() and _git(tree, "rev-parse", "--abbrev-ref", "HEAD") == "agent-work"


def test_a_named_branch_lands_from_anywhere(repo):
    tree = _setup(repo)
    _commit(tree, "a.py")
    code, out, err = run_cli(repo, "merge", "T1", "--branch", "agent-work")
    assert code == OK, out + err
    assert _git(repo, "show", "main:a.py") == "x = 1"


def test_from_the_primary_with_no_branch_it_refuses_and_says_how(repo):
    tree = _setup(repo)
    _commit(tree, "a.py")
    code, out, err = run_cli(repo, "merge", "T1")
    assert code == REFUSED, out + err
    assert "--branch" in out + err
    assert _merged_events(repo) == []


def test_a_branch_with_nothing_new_is_nothing_to_merge(repo):
    tree = _setup(repo)
    code, out, err = run_cli(tree, "merge", "T1")
    assert code == NOTHING, out + err
    assert _merged_events(repo) == []


def test_uncommitted_work_in_the_branchs_tree_is_refused_and_named(repo):
    tree = _setup(repo)
    _commit(tree, "a.py")
    (tree / "a.py").write_text("x = 2\n")
    code, out, err = run_cli(tree, "merge", "T1")
    assert code == REFUSED, out + err
    assert "a.py" in out + err
    assert _merged_events(repo) == []


def test_paths_outside_the_items_globs_are_named(repo):
    """A borrowed branch can carry more than this item's work -- another item's
    commits, made in the same tree. The merge says which paths the item never
    declared, so landing someone else's work is at least not silent."""
    tree = _setup(repo)
    _commit(tree, "a.py")
    _commit(tree, "b.py")
    code, out, err = run_cli(repo, "--json", "merge", "T1", "--branch", "agent-work")
    assert code == OK, out + err
    assert json.loads(out)["outside_globs"] == ["b.py"]


def test_an_item_with_its_own_tree_refuses_a_different_branch(repo):
    run_cli(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    _git(repo, "branch", "elsewhere")
    run_cli(repo, "task", "add", "T1", "--title", "add a", "--globs", "a.py")
    code, out, err = run_cli(repo, "claim", "T1")
    assert code == OK, out + err
    code, out, err = run_cli(repo, "merge", "T1", "--branch", "elsewhere")
    assert code == REFUSED, out + err
    assert "elsewhere" in out + err


def _another_items_tree(repo: Path) -> Path:
    """A second harness tree, adopted by another open item, T2."""
    other = repo.parent / "t2-tree"
    _git(repo, "worktree", "add", "-q", str(other), "-b", "t2-work")
    run_cli(repo, "task", "add", "T2", "--title", "t2", "--globs", "z.py")
    code, out, err = run_cli(other, "claim", "T2")
    assert code == OK and "adopted" in out, out + err
    return other


def test_standing_in_another_items_tree_does_not_land_its_branch(repo):
    tree = _setup(repo)
    _commit(tree, "a.py")
    other = _another_items_tree(repo)
    _commit(other, "z.py")
    code, out, err = run_cli(other, "merge", "T1")
    assert code == REFUSED, out + err
    assert "T2" in out + err
    assert _merged_events(repo) == []

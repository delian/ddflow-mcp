"""A worktree git cannot read is never "clean" (bug B028b11b4cb).

`W.dirty` returned ``[]`` when ``git status`` failed, and `cleanup`'s survey turned that
(and an ``ahead`` of -1, clamped to 0) into "fully merged -- safe to remove": a tree whose
``.git`` file is broken, whose directory is unreadable, or whose git cannot run was
reported as an empty leftover and offered for removal while it held uncommitted work.
"Could not measure" is unknown, and unknown is never clean.

Real git repositories: the property is what git can and cannot read on disk.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ddflow import api
from ddflow.infra import worktree as W


def _git(where: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(where), *args], check=True, capture_output=True)


def _broken_tree(repo: Path) -> Path:
    """A ddflow-prefixed worktree holding an edit, whose `.git` file points nowhere."""
    _git(repo, "commit", "-q", "--allow-empty", "-m", "base")
    tree = repo.parent / "broken-tree"
    _git(repo, "worktree", "add", "-q", str(tree), "-b", "ddflow/broken")
    (tree / "work.txt").write_text("uncommitted work that exists nowhere else\n")
    (tree / ".git").write_text("gitdir: /nonexistent/ddflow-B028b11b4cb\n")
    return tree


def test_dirty_of_an_unreadable_tree_is_not_empty(repo):
    tree = _broken_tree(repo)
    wt = W.Worktree(item="x", path=tree, branch="ddflow/broken", base="main")

    lines = W.dirty(wt)

    assert lines, "a tree git could not read was reported clean"
    assert W.unreadable(lines)
    assert not W.unreadable(["?? work.txt"]), "an ordinary untracked file is readable"
    assert not lines[0].startswith(("??", "!!")), "a status-code filter must not drop it"
    assert W.dirty(wt, untracked=False), "the tracked-only question fails closed too"


def test_cleanup_never_offers_an_unreadable_tree_for_removal(repo):
    from conftest import run_cli

    run_cli(repo, "init")
    tree = _broken_tree(repo)

    out = api.cleanup(repo, apply=False, agent="sweeper")

    rows = [t for t in out.data["trees"] if Path(t["path"]).resolve() == tree.resolve()]
    assert rows, f"cleanup did not report the tree at all: {out.data['trees']}"
    row = rows[0]
    assert row["action"] == "", f"an unreadable tree was offered for removal: {row}"
    assert row["kind"] == "unreadable", row
    assert "LEAVE ALONE" in row["done"], row
    assert out.data["needs_human"] >= 1, "an unreadable tree needs a human to look"


def test_remove_refuses_a_tree_whose_ahead_count_is_unknown(repo):
    _git(repo, "commit", "-q", "--allow-empty", "-m", "base")
    tree = repo.parent / "lost-base"
    _git(repo, "worktree", "add", "-q", str(tree), "-b", "ddflow/lost-base")
    # git status works, but the base the commits are counted against does not exist.
    wt = W.Worktree(item="x", path=tree, branch="ddflow/lost-base", base="no-such-base")
    assert W.ahead(wt) == -1, "the fixture must make the ahead count unmeasurable"

    r = W.remove(repo, None, wt)

    assert not r.ok, "a tree whose unmerged commits could not be counted was removed"
    assert tree.exists()

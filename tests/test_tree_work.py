"""A worktree git cannot read is never "clean" (bug B028b11b4cb), and one measurement of what
a worktree holds (B-uni-tree-lifecycle.2-work).

`W.dirty` returned ``[]`` when ``git status`` failed, and `cleanup`'s survey turned that
(and an ``ahead`` of -1, clamped to 0) into "fully merged -- safe to remove": a tree whose
``.git`` file is broken, whose directory is unreadable, or whose git cannot run was
reported as an empty leftover and offered for removal while it held uncommitted work.
"Could not measure" is unknown, and unknown is never clean.

Real git repositories: the property is what git can and cannot read on disk.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path, PurePosixPath

import pytest

from ddflow import api
from ddflow.infra import git as G
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
    assert lines[0][0] not in " MTADRCU?!", "a porcelain status filter must not drop it"
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


# -- one measurement of what a worktree holds (B-uni-tree-lifecycle.2-work) ------------------
#
# `worktree.tree_work` replaces three private measurements: lease recovery's
# (`leases._measure`: the count of uncommitted entries, commits ahead), the cleanup survey's
# (`cleanup._measure`: `W.dirty`, ahead, behind) and the onboarding sweep's
# (`onboard._status`: readable, work, non-cache ignored files). The oracles below are those
# three, and the one measurement is checked against all of them over trees in every state
# those callers met.

CACHES = frozenset(
    {
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".venv",
        "venv",
        "node_modules",
        ".cache",
    }
)


def _commit(path: Path, name: str, text: str = "x\n") -> None:
    (path / name).write_text(text)
    _git(path, "add", name)
    _git(path, "commit", "-qm", f"add {name}")


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # the user's own git config (a global excludes file, status.showUntrackedFiles) must not
    # change what the fixture's states mean
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    t = tmp_path / "t"
    t.mkdir()
    _git(t, "init", "-q", "-b", "main")
    _git(t, "config", "user.email", "t@e.com")
    _git(t, "config", "user.name", "T")
    _git(t, "config", "commit.gpgsign", "false")
    (t / ".gitignore").write_text(".env\n.venv/\n__pycache__/\nbuild/\n")
    _git(t, "add", ".gitignore")
    _commit(t, "a.txt")
    _commit(t, "b.txt")
    _git(t, "branch", "base")  # the base the feature work is measured against
    return t


# -- the three oracles, verbatim ----------------------------------------------------------


def oracle_recovery(path: Path, base: str):
    """`leases._measure`: (entries counted, commits ahead, git's stderr)."""
    status = G.status_run(path)
    entries = G.parse_status(status)
    probe = W.Worktree(item="x", path=path, branch="", base=base)
    return (len(entries) if entries is not None else -1), W.ahead(probe), status.err


def oracle_cleanup(path: Path, base: str):
    """`cleanup._measure`: (dirty lines, ahead, behind)."""
    wt = W.Worktree(item="x", path=path, branch="", base=base)
    return W.dirty(wt), W.ahead(wt), W.behind(wt)


def _is_cache(name: str) -> bool:
    return any(part in CACHES for part in PurePosixPath(name).parts)


def oracle_onboard(path: Path):
    """`onboard._status`: (readable, work, non-cache ignored)."""
    entries = G.status(path, ignored="matching")
    if entries is None:
        return False, [], []
    work: list[str] = []
    ignored: list[str] = []
    for e in entries:
        if _is_cache(e.path):
            continue
        (ignored if e.ignored else work).append(e.path)
    return True, work, ignored


# -- the states ---------------------------------------------------------------------------


def _clean(t: Path) -> None:
    pass


def _modified(t: Path) -> None:
    (t / "a.txt").write_text("changed\n")


def _staged(t: Path) -> None:
    (t / "new.txt").write_text("n\n")
    _git(t, "add", "new.txt")


def _untracked_file(t: Path) -> None:
    (t / "scratch.txt").write_text("s\n")


def _untracked_dir(t: Path) -> None:
    (t / "newdir").mkdir()
    (t / "newdir" / "f.txt").write_text("f\n")


def _untracked_cache(t: Path) -> None:
    (t / "node_modules").mkdir()  # not in .gitignore: an untracked cache
    (t / "node_modules" / "m.js").write_text("c")


def _ignored_work(t: Path) -> None:
    (t / ".env").write_text("SECRET=1\n")


def _ignored_cache(t: Path) -> None:
    (t / ".venv").mkdir()
    (t / ".venv" / "bin").write_text("v")


def _ignored_dir_work(t: Path) -> None:
    (t / "build").mkdir()
    (t / "build" / "out.o").write_text("o")


def _deleted(t: Path) -> None:
    (t / "a.txt").unlink()


def _renamed(t: Path) -> None:
    _git(t, "mv", "a.txt", "renamed.txt")


def _ahead(t: Path) -> None:
    _commit(t, "c.txt")
    _commit(t, "d.txt")


def _behind(t: Path) -> None:
    _git(t, "checkout", "-q", "base")
    _commit(t, "base-only.txt")
    _git(t, "checkout", "-q", "main")


def _diverged(t: Path) -> None:
    _behind(t)
    _ahead(t)
    _modified(t)
    _untracked_file(t)
    _ignored_work(t)


def _unreadable(t: Path) -> None:
    (t / ".git").rename(t / ".git-moved")


STATES = [
    _clean,
    _modified,
    _staged,
    _untracked_file,
    _untracked_dir,
    _untracked_cache,
    _ignored_work,
    _ignored_cache,
    _ignored_dir_work,
    _deleted,
    _renamed,
    _ahead,
    _behind,
    _diverged,
    _unreadable,
]


#: The commit counts that are LITERALLY right in a state (everything else sits at the base:
#: 0 ahead, 0 behind). The old and the new code share `rev-list` plumbing, so equal to each
#: other would not catch both reading -1; these do.
COUNTS = {"_ahead": (2, 0), "_behind": (0, 1), "_diverged": (2, 1), "_unreadable": (-1, -1)}


@pytest.mark.parametrize("state", STATES, ids=lambda f: f.__name__)
def test_the_one_measurement_equals_the_three_old_ones(tree: Path, state) -> None:
    state(tree)
    tw = W.tree_work(tree, "base", behind=True)
    count, ahead, err = oracle_recovery(tree, "base")
    lines, ahead_c, behind_c = oracle_cleanup(tree, "base")
    readable, work, ignored = oracle_onboard(tree)

    assert tw.readable is readable
    assert (tw.ahead, tw.behind) == COUNTS.get(state.__name__, (0, 0))
    assert (len(tw.dirty) if tw.readable else -1) == count  # lease recovery
    assert tw.ahead == ahead == ahead_c
    assert tw.behind == behind_c
    if tw.readable:
        assert tw.dirty == lines  # cleanup's list
    else:
        assert W.unreadable(lines) and tw.err == err
        assert lines == [f"{W.UNREADABLE}git status failed: {tw.msg}"]
    assert (tw.work, tw.ignored) == (work, ignored)  # onboarding


def test_a_clean_tree_holds_nothing_and_an_unknown_base_is_unknown_not_zero(tree: Path) -> None:
    tw = W.tree_work(tree, "base")
    assert (tw.readable, tw.dirty, tw.work, tw.ignored, tw.ahead) == (True, [], [], [], 0)
    unknown = W.tree_work(tree, "no-such-ref")
    assert unknown.readable and unknown.ahead == -1, "git could not count: -1, never 0"
    none = W.tree_work(tree)
    assert none.ahead == -1 and none.behind == -1, "no base: nothing was counted"


def test_caches_are_not_work_but_remain_in_the_raw_dirty_lines(tree: Path) -> None:
    _untracked_cache(tree)
    tw = W.tree_work(tree, "base")
    assert tw.work == [] and tw.ignored == []
    assert tw.dirty == ["?? node_modules/"]


def test_ignored_work_is_reported_apart_from_what_git_counts_as_changed(tree: Path) -> None:
    _ignored_work(tree)
    tw = W.tree_work(tree, "base")
    assert tw.dirty == [] and tw.work == [] and tw.ignored == [".env"]

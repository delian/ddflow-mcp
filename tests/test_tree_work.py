"""One measurement of what a worktree holds (B-uni-tree-lifecycle.2-work).

`worktree.tree_work` replaces three private measurements: lease recovery's (`leases._measure`:
the count of uncommitted entries, commits ahead), the cleanup survey's (`cleanup._measure`:
`W.dirty`, ahead, behind) and the onboarding sweep's (`onboard._status`: readable, work,
non-cache ignored files). The oracles below are those three, kept verbatim, and the one
measurement is checked against all of them over trees in every state those callers met.
"""

from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath

import pytest

from ddflow.infra import git as G
from ddflow.infra import worktree as W

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


def _git(path: Path, *argv: str) -> None:
    subprocess.run(["git", "-C", str(path), *argv], check=True, capture_output=True)


def _commit(path: Path, name: str, text: str = "x\n") -> None:
    (path / name).write_text(text)
    _git(path, "add", name)
    _git(path, "commit", "-qm", f"add {name}")


@pytest.fixture
def tree(tmp_path: Path) -> Path:
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


@pytest.mark.parametrize("state", STATES, ids=lambda f: f.__name__)
def test_the_one_measurement_equals_the_three_old_ones(tree: Path, state) -> None:
    state(tree)
    tw = W.tree_work(tree, "base", behind=True)
    count, ahead, err = oracle_recovery(tree, "base")
    lines, ahead_c, behind_c = oracle_cleanup(tree, "base")
    readable, work, ignored = oracle_onboard(tree)

    assert tw.readable is readable
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

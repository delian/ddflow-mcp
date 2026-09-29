"""The commit checks read what THIS commit stages: the committing tree's index against
that tree's own HEAD (bug Bba366d9893).

The CLI resolves ``repo`` to the PRIMARY checkout, and `staged_paths` ran
`git -C <primary> diff --cached`. Inside a linked worktree that compared the worktree's
index (git exports `GIT_INDEX_FILE` to the hook) with the PRIMARY's HEAD: every file main
changed since the branch point, and every change the branch had already committed, read
as staged. Under `commit_without_lease = "block"` a branch behind main was refused for
files it never touched. Run from a worktree with no git environment at all (an agent
running `ddflow hooks check-commit` by hand), it read the primary's index instead.

A real `git commit` also exports `GIT_DIR` (the worktree's gitdir) to its hooks, which
happened to steer `-C <primary>` back to the worktree's HEAD; the probes below set
exactly what a hook runner that keeps `GIT_INDEX_FILE` but not `GIT_DIR` would leave,
and what a hand-run check has (nothing). The fix does not depend on either variable.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.config import Config
from ddflow.infra import worktree as W
from ddflow.infra.log import EventLog
from ddflow.services import enforce as E
from ddflow.services import leases as L

_GIT_ENV = ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_COMMON_DIR", "DDFLOW_AGENT")


def _git(cwd: Path, *args: str, env: dict | None = None) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True, env=env
    ).stdout.strip()


def _index_of(tree: Path) -> str:
    """`GIT_INDEX_FILE` exactly as git exports it to a pre-commit hook in a linked
    worktree: the absolute path of that tree's own index."""
    return _git(tree, "rev-parse", "--path-format=absolute", "--git-path", "index")


def _config(repo: Path, **enforce: str) -> Config:
    body = "".join(
        f'{k} = "{v}"\n' for k, v in {"commit_without_lease": "block", **enforce}.items()
    )
    (repo / ".ddflow" / "config.toml").write_text("[enforce]\n" + body)
    return Config.load(repo)


@pytest.fixture
def behind(repo: Path, cfg: Config, monkeypatch) -> tuple[Path, Path]:
    """(primary, worktree). The worktree's branch forked before main changed
    shared.txt; its lease covers mine.txt; it has staged only mine.txt; the cwd is the
    worktree, as a hook's is."""
    for k in _GIT_ENV:
        monkeypatch.delenv(k, raising=False)
    (repo / ".ddflow").mkdir()
    _config(repo)
    (repo / "shared.txt").write_text("v1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base", "--no-verify")
    run_cli(repo, "phase", "add", "P1", "--title", "c")
    run_cli(repo, "task", "add", "P1.T1", "--phase", "P1", "--globs", "mine.txt")
    wt = W.create(repo, cfg, "P1.T1")
    L.acquire(
        EventLog(repo, "the-claimer"),
        cfg,
        "P1.T1",
        holder="the-claimer",
        worktree=W.store_path(repo, wt.path),
        branch=wt.branch,
        globs=["mine.txt"],
    )
    (repo / "shared.txt").write_text("v2, changed on main after the branch point\n")
    _git(repo, "commit", "-qam", "main moves on", "--no-verify")
    (wt.path / "mine.txt").write_text("mine\n")
    _git(wt.path, "add", "mine.txt")
    monkeypatch.chdir(wt.path)
    return repo, wt.path


def test_a_worktree_behind_main_stages_only_its_own_file(behind, monkeypatch):
    """The regression: GIT_INDEX_FILE set as git sets it, cwd in the worktree."""
    repo, wt = behind
    monkeypatch.setenv("GIT_INDEX_FILE", _index_of(wt))
    assert E.staged_paths(repo) == ["mine.txt"]
    assert E.check_commit(repo, Config.load(repo)) == (0, "")


def test_a_check_run_by_hand_in_a_worktree_reads_that_worktrees_index(behind):
    """No git environment at all: the primary's index is not this commit's."""
    repo, _wt = behind
    (repo / "primary-only.txt").write_text("x\n")
    _git(repo, "add", "primary-only.txt")
    assert E.staged_paths(repo) == ["mine.txt"]
    assert E.check_commit(repo, Config.load(repo)) == (0, "")


def test_an_unleased_file_staged_in_the_worktree_is_still_refused(behind, monkeypatch):
    repo, wt = behind
    (wt / "other.txt").write_text("not mine\n")
    _git(wt, "add", "other.txt")
    monkeypatch.setenv("GIT_INDEX_FILE", _index_of(wt))
    assert E.staged_paths(repo) == ["mine.txt", "other.txt"]
    code, msg = E.check_commit(repo, Config.load(repo))
    assert code == 1, msg
    assert "other.txt" in msg and "shared.txt" not in msg, msg


def test_the_branchs_own_earlier_commits_are_not_staged_again(behind, monkeypatch):
    """A change the branch already committed is not part of the next commit -- even with
    the branch fully up to date with main."""
    repo, wt = behind
    _git(wt, "reset", "-q")
    _git(wt, "merge", "-q", "--no-edit", "main")
    (wt / "earlier.txt").write_text("committed before\n")
    _git(wt, "add", "earlier.txt")
    _git(wt, "commit", "-qm", "earlier", "--no-verify")
    _git(wt, "add", "mine.txt")
    monkeypatch.setenv("GIT_INDEX_FILE", _index_of(wt))
    assert E.staged_paths(repo) == ["mine.txt"]
    assert E.check_commit(repo, Config.load(repo)) == (0, "")


def test_a_temporary_index_is_honoured_not_dropped(behind, monkeypatch):
    """`git commit -a` and `git commit <paths>` hand the hook a TEMPORARY index
    (`index.lock`, `next-index-*.lock`): that one is the commit, not the tree's own."""
    repo, wt = behind
    tmp = str(wt.parent / "next-index-test.lock")
    env = {**os.environ, "GIT_INDEX_FILE": tmp}
    _git(wt, "read-tree", "HEAD", env=env)
    (wt / "partial.txt").write_text("p\n")
    _git(wt, "add", "partial.txt", env=env)
    monkeypatch.setenv("GIT_INDEX_FILE", tmp)
    assert E.staged_paths(repo) == ["partial.txt"]


def test_a_commit_in_the_primary_checkout_itself(behind, monkeypatch):
    """In the primary git exports a RELATIVE `GIT_INDEX_FILE=.git/index`."""
    repo, _wt = behind
    monkeypatch.chdir(repo)
    (repo / "p.txt").write_text("p\n")
    _git(repo, "add", "p.txt")
    monkeypatch.setenv("GIT_INDEX_FILE", ".git/index")
    assert E.staged_paths(repo) == ["p.txt"]


@pytest.mark.parametrize("where", ["not-git", "foreign-repo"])
def test_outside_the_repository_it_falls_back_to_repo(behind, monkeypatch, tmp_path, where):
    """A cwd that is no tree of THIS repository says nothing about the commit: read
    ``repo``, as before, rather than some other repository's index."""
    repo, _wt = behind
    elsewhere = tmp_path / where
    elsewhere.mkdir()
    if where == "foreign-repo":
        _git(elsewhere, "init", "-q")
        (elsewhere / "foreign.txt").write_text("f\n")
        _git(elsewhere, "add", "foreign.txt")
    monkeypatch.chdir(elsewhere)
    (repo / "p.txt").write_text("p\n")
    _git(repo, "add", "p.txt")
    assert E.staged_paths(repo) == ["p.txt"]


def test_an_unreadable_worktree_index_is_still_could_not_tell(behind, monkeypatch):
    """None means "could not tell", and the checks refuse on it -- in a worktree too."""
    repo, wt = behind
    Path(_index_of(wt)).write_bytes(b"garbage")
    monkeypatch.setenv("GIT_INDEX_FILE", _index_of(wt))
    assert E.staged_paths(repo) is None
    code, msg = E.check_commit(repo, Config.load(repo))
    assert code == 1 and "could not report" in msg, msg


def test_doc_sync_judges_this_commit_not_the_branchs_history(behind, monkeypatch):
    """check_docs diffed the primary's HEAD too: a name the branch removed in an EARLIER
    commit was charged to a commit that stages only mine.txt."""
    repo, wt = behind
    _git(wt, "reset", "-q")
    (repo / "lib.py").write_text("def old_helper():\n    return 1\n")
    (repo / "guide.md").write_text("Call `old_helper` to get one.\n")
    _git(repo, "add", "lib.py", "guide.md")
    _git(repo, "commit", "-qm", "helper", "--no-verify")
    _git(wt, "merge", "-q", "--no-edit", "main")
    (wt / "lib.py").write_text("def new_helper():\n    return 1\n")
    _git(wt, "commit", "-qam", "rename; the doc is left behind", "--no-verify")
    _git(wt, "add", "mine.txt")
    monkeypatch.setenv("GIT_INDEX_FILE", _index_of(wt))
    cfg = _config(repo, stale_docs="block")
    assert E.check_docs(repo, cfg) == (0, "")


def test_a_view_the_branch_committed_earlier_is_not_checked_again(behind, monkeypatch):
    """check_views listed the same wrong set, and refused a stale view this commit does
    not stage."""
    from ddflow.views.markdown import GENERATED

    repo, wt = behind
    (wt / "QUEUE.md").write_text(GENERATED + "\nstale\n")
    _git(wt, "add", "QUEUE.md")
    _git(wt, "commit", "-qm", "a view", "--no-verify")
    monkeypatch.setenv("GIT_INDEX_FILE", _index_of(wt))
    assert E.check_views(repo, Config.load(repo)) == (0, "")


def test_a_view_staged_by_hand_in_a_worktree_is_checked_against_that_trees_log(behind):
    """No git environment: the view and the log it must agree with are both read from
    the worktree's index, the log files from the primary (what the view renders from).
    With a shard the worktree's index does not carry, the view is refused; staged, it
    passes."""
    import shutil

    from ddflow.core.model import fold
    from ddflow.views.markdown import render_views

    repo, wt = behind
    events = repo / ".ddflow" / "events"

    def stage_view() -> None:
        state = fold(EventLog(repo, "x").read_all(), strict=False)
        (wt / "QUEUE.md").write_text(render_views(state, Config.load(repo, env={}))["QUEUE.md"])
        _git(wt, "add", "QUEUE.md")

    shutil.copytree(events, wt / ".ddflow" / "events")
    _git(wt, "add", "-f", ".ddflow/events")
    run_cli(repo, "task", "add", "P1.T2", "--phase", "P1", "--globs", "b.txt")
    stage_view()
    code, msg = E.check_views(repo, Config.load(repo))
    assert code == 1 and "does not record" in msg, msg
    shutil.rmtree(wt / ".ddflow" / "events")
    shutil.copytree(events, wt / ".ddflow" / "events")
    _git(wt, "add", "-f", ".ddflow/events")
    assert E.check_views(repo, Config.load(repo)) == (0, "")


def test_drift_is_not_measured_in_another_repository(behind, monkeypatch, tmp_path):
    """The drift check reads HEAD of the committing tree: a cwd in some other repository
    is "could not tell", not that repository's drift."""
    repo, _wt = behind
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-q")
    monkeypatch.chdir(other)
    code, msg = E.check_drift(repo, Config.load(repo))
    assert code == 0 and "could not tell which working tree" in msg, msg


# -- end to end: a real `git commit` through ddflow's installed hook --------------------


def _hook_commit(tree: Path, message: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in _GIT_ENV}
    return subprocess.run(
        ["git", "-C", str(tree), "commit", "-m", message],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )


@pytest.mark.parametrize("scrub_git_dir", [False, True], ids=["as-git-runs-it", "no-GIT_DIR"])
def test_a_real_commit_in_a_worktree_behind_main(behind, scrub_git_dir):
    """Through the installed hook. `as-git-runs-it` passed before the fix too, because git
    exports GIT_DIR; `no-GIT_DIR` is a hook runner that drops it and keeps
    GIT_INDEX_FILE, which refused the commit for shared.txt."""
    repo, wt = behind
    assert E.install(repo).startswith("installed")
    if scrub_git_dir:
        hook = E.hooks_dir(repo) / "pre-commit"
        hook.write_text(
            "#!/bin/sh\nunset GIT_DIR\n" + E.command_line("hooks check-commit", exec_=True) + "\n"
        )
    r = _hook_commit(wt, "mine")
    assert r.returncode == 0, r.stderr
    (wt / "other.txt").write_text("not mine\n")
    _git(wt, "add", "other.txt")
    r = _hook_commit(wt, "not mine")
    assert r.returncode != 0, "an unleased file was committed under block"
    assert "other.txt" in r.stderr and "shared.txt" not in r.stderr, r.stderr

"""B-merge-stash-failed-transient: `git merge` runs `git stash create` when the primary has
uncommitted tracked work; if another process holds `.git/index.lock` at that moment it dies
with `fatal: stash failed` and merges nothing. The identical retry succeeds, so the merge
retries a bounded number of times and, when it gives up, says what happened."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_flow import _commit, _git

from ddflow.config import Config
from ddflow.infra import worktree as W


def _setup(repo):
    cfg = Config()
    wt = W.create(repo, cfg, "T1")
    _commit(wt.path, "f.py", "x\n", "feat: f")
    _commit(repo, "g.py", "y\n", "main moves")  # a real merge, not a fast-forward
    (repo / "README.md").write_text("dirty tracked\n")  # makes git save state
    return cfg, wt


def _lock(repo) -> Path:
    lock = Path(_git(repo, "rev-parse", "--absolute-git-dir")) / "index.lock"
    lock.write_text("")
    return lock


def test_a_stash_failure_from_a_briefly_held_index_lock_is_retried(repo, monkeypatch):
    cfg, wt = _setup(repo)
    lock = _lock(repo)
    pauses = []

    def release_on_first_pause(seconds):  # only reached AFTER a failed attempt: deterministic
        pauses.append(seconds)
        lock.unlink(missing_ok=True)

    monkeypatch.setattr(W.time, "sleep", release_on_first_pause)
    r = W.merge(repo, cfg, wt)
    assert pauses, "the first attempt must have failed on the lock, or this proves nothing"
    assert r.ok, r.err or r.out
    assert _git(repo, "show", "HEAD:f.py") == "x"
    assert (repo / "README.md").read_text() == "dirty tracked\n"


def test_a_stash_failure_that_never_clears_is_reported_with_its_cause(repo, monkeypatch):
    cfg, wt = _setup(repo)
    head = _git(repo, "rev-parse", "HEAD")
    _lock(repo)
    pauses = []
    monkeypatch.setattr(W.time, "sleep", pauses.append)
    r = W.merge(repo, cfg, wt)
    assert len(pauses) == W.STASH_RETRIES - 1  # bounded
    assert not r.ok
    msg = r.err or r.out
    assert "stash failed" in msg and "index.lock" in msg and "nothing was merged" in msg, msg
    assert _git(repo, "rev-parse", "HEAD") == head and not (repo / "f.py").exists()


def test_a_conflict_in_a_file_named_stash_failed_is_not_retried(repo, monkeypatch):
    """rubber_duck round 1: only git's own `fatal: stash failed` line is transient."""
    cfg = Config()
    wt = W.create(repo, cfg, "T1")
    _commit(wt.path, "stash failed", "branch\n", "feat: f")
    _commit(repo, "stash failed", "main\n", "main: same file")
    pauses = []
    monkeypatch.setattr(W.time, "sleep", pauses.append)
    r = W.merge(repo, cfg, wt)
    assert not r.ok and not pauses
    assert "CONFLICT" in (r.err + r.out)


def test_a_blocking_file_named_like_the_error_is_not_retried(repo, monkeypatch):
    """critic round 2: git lists a blocking file indented, so its NAME is not the error line."""
    cfg = Config()
    name = "fatal: stash failed"
    _commit(repo, name, "base\n", "main: add")
    wt = W.create(repo, cfg, "T1")
    _commit(repo, name, "main moved\n", "main: edit")
    _commit(wt.path, name, "branch\n", "feat: edit")
    (repo / name).write_text("dirty local\n")  # the merge would overwrite it: git refuses
    pauses = []
    monkeypatch.setattr(W.time, "sleep", pauses.append)
    r = W.merge(repo, cfg, wt)
    assert not r.ok and not pauses, (r.err, r.out)
    assert "index.lock" not in (r.err + r.out)


def test_a_merge_already_begun_is_never_rerun_whatever_its_output_says(repo, monkeypatch):
    """rubber_duck round 2: a path can make a CONFLICT's output carry the error line; the
    merge then has MERGE_HEAD and the retry would hide the conflict."""
    cfg = Config()
    wt = W.create(repo, cfg, "T1")
    _commit(wt.path, "c.txt", "branch\n", "feat: c")
    _commit(repo, "c.txt", "main\n", "main: c")
    real = W.git
    seen = []

    def git(tree, *a, **kw):
        r = real(tree, *a, **kw)
        if a[:1] == ("merge",) and not r.ok:
            seen.append(1)
            return W.GitResult(r.code, r.out, r.err + "\nfatal: stash failed\n")
        return r

    monkeypatch.setattr(W, "git", git)
    monkeypatch.setattr(W.time, "sleep", lambda s: None)
    r = W.merge(repo, cfg, wt)
    assert len(seen) == 1 and not r.ok  # attempted once; MERGE_HEAD existed: not retried

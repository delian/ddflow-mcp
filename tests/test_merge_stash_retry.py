"""B-merge-stash-failed-transient: `git merge` runs `git stash create` when the primary has
uncommitted tracked work; if another process holds `.git/index.lock` at that moment it dies
with `fatal: stash failed` and merges nothing. The identical retry succeeds, so the merge
retries a bounded number of times and, when it gives up, says what happened."""

from __future__ import annotations

import sys
import threading
import time
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


def _hold_lock(repo, seconds):
    lock = Path(_git(repo, "rev-parse", "--absolute-git-dir")) / "index.lock"
    lock.write_text("")

    def release():
        time.sleep(seconds)
        lock.unlink(missing_ok=True)

    t = threading.Thread(target=release)
    t.start()
    return t


def test_a_stash_failure_from_a_briefly_held_index_lock_is_retried(repo, monkeypatch):
    monkeypatch.setattr(W, "STASH_RETRY_PAUSE_S", 0.2, raising=False)
    cfg, wt = _setup(repo)
    t = _hold_lock(repo, 0.5)
    r = W.merge(repo, cfg, wt)
    t.join()
    assert r.ok, r.err or r.out
    assert _git(repo, "show", "HEAD:f.py") == "x"
    assert (repo / "README.md").read_text() == "dirty tracked\n"


def test_a_stash_failure_that_never_clears_is_reported_with_its_cause(repo, monkeypatch):
    monkeypatch.setattr(W, "STASH_RETRY_PAUSE_S", 0.01, raising=False)
    cfg, wt = _setup(repo)
    head = _git(repo, "rev-parse", "HEAD")
    t = _hold_lock(repo, 3)
    r = W.merge(repo, cfg, wt)
    t.join()
    assert not r.ok
    msg = r.err or r.out
    assert "stash failed" in msg and "index.lock" in msg and "nothing was merged" in msg, msg
    assert _git(repo, "show", "HEAD:g.py") == "y" and not (repo / "f.py").exists()  # unmerged
    assert _git(repo, "rev-parse", "HEAD") == head

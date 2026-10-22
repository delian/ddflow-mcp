"""B-merge-stash-failed-transient: `git merge` runs `git stash create` when the primary has
uncommitted tracked work; if another process holds `.git/index.lock` at that moment it dies
with `fatal: stash failed` and merges nothing. The identical retry succeeds, so the merge
retries a bounded number of times and, when it gives up, says what happened."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_flow import _commit, _git

from ddflow.config import Config
from ddflow.infra import worktree as W


def _patch_sleep(monkeypatch, fn):
    """Replace the sleep of the WORKTREE MODULE only: patching `time.sleep` itself would hand
    this test every other thread's sleep too (B-merge-stash-retry-tests-patch-global-sleep)."""
    monkeypatch.setattr(W, "time", SimpleNamespace(sleep=fn))


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

    _patch_sleep(monkeypatch, release_on_first_pause)
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
    _patch_sleep(monkeypatch, pauses.append)
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
    _patch_sleep(monkeypatch, pauses.append)
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
    _patch_sleep(monkeypatch, pauses.append)
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
    _patch_sleep(monkeypatch, lambda s: None)
    r = W.merge(repo, cfg, wt)
    assert len(seen) == 1 and not r.ok  # attempted once; MERGE_HEAD existed: not retried


def test_a_refusal_listing_a_file_named_with_the_error_line_is_not_retried(repo, monkeypatch):
    """rubber_duck round 3: a listed file whose name embeds a newline can put the sentence on
    a line of its own; the surrounding refusal (indented file, stdout) says it is not the error."""
    cfg = Config()
    wt = W.create(repo, cfg, "T1")
    _commit(wt.path, "f.py", "x\n", "feat: f")
    seen = []
    real = W.git

    def git(tree, *a, **kw):
        if a[:1] == ("merge",):
            seen.append(1)
            err = (
                "error: The following untracked working tree files would be overwritten by "
                "merge:\n\ta\nfatal: stash failed\nPlease move or remove them.\nAborting\n"
            )
            return W.GitResult(1, "", err)
        return real(tree, *a, **kw)

    monkeypatch.setattr(W, "git", git)
    _patch_sleep(monkeypatch, lambda s: None)
    r = W.merge(repo, cfg, wt)
    assert len(seen) == 1 and not r.ok


def test_the_multi_line_lock_explanation_before_the_sentence_is_still_retried(repo, monkeypatch):
    """rubber_duck round 4: git explains the lock in unprefixed lines before the sentence."""
    cfg = Config()
    wt = W.create(repo, cfg, "T1")
    _commit(wt.path, "f.py", "x\n", "feat: f")
    real = W.git
    seen = []
    err = (
        "fatal: Unable to create '.git/index.lock': File exists.\n"
        "Another git process seems to be running in this repository, e.g.\n"
        "an editor opened by 'git commit'. Please make sure all processes\n"
        "are finished and then try again.\nfatal: stash failed\n"
    )

    def git(tree, *a, **kw):
        if a[:1] == ("merge",) and not seen:
            seen.append(1)
            return W.GitResult(128, "", err)
        return real(tree, *a, **kw)

    monkeypatch.setattr(W, "git", git)
    _patch_sleep(monkeypatch, lambda s: None)
    r = W.merge(repo, cfg, wt)
    assert r.ok, r.err or r.out  # the second attempt ran and merged


def test_other_threads_sleeping_do_not_count_as_the_merge_pauses(repo, monkeypatch):
    """A busy neighbour thread sleeps all the while; only the merge's own pauses are counted."""
    cfg, wt = _setup(repo)
    _lock(repo)
    pauses = []
    _patch_sleep(monkeypatch, pauses.append)
    stop = threading.Event()

    def neighbour():
        while not stop.is_set():
            time.sleep(0.0005)

    t = threading.Thread(target=neighbour)
    t.start()
    try:
        r = W.merge(repo, cfg, wt)
    finally:
        stop.set()
        t.join()
    assert not r.ok and len(pauses) == W.STASH_RETRIES - 1

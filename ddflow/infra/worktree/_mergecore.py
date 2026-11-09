"""The merge attempt itself — retried only while git reports a transient pre-merge failure
that merged nothing (``stash failed``, or a lost ``MERGE_MSG`` read).

The retry loop is also the one place this package calls ``time.sleep``, and it, ``git``
and ``GitResult`` are read through ``_W`` so the test-suite's ``monkeypatch.setattr(W,
"git"/"time", ...)`` sees them exactly as it did when they were module globals in one file.
"""

from __future__ import annotations

from pathlib import Path

from .. import git as G
from .. import worktree as _W

#: What `merge` and `remove` return to mean "refused on a precondition" as opposed to
#: "git failed". It maps to the surfaces' REFUSED (3), and naming it here — beside the
#: functions that return it — keeps the two exit vocabularies from being silently
#: conflated by whoever reads only one of them.
GIT_REFUSED = 2

#: B-merge-stash-failed-transient: a real merge into a tree with uncommitted tracked work makes
#: git run `git stash create` first; while another process holds `.git/index.lock` (another
#: agent's `git status`/`add` in the primary) that dies with `fatal: stash failed` BEFORE
#: anything is merged. The next attempt, moments later, succeeds. So the merge is attempted a
#: bounded number of times, pausing ``STASH_RETRY_PAUSE_S * 2**n`` between them.
STASH_RETRIES = 4
STASH_RETRY_PAUSE_S = 0.25


def _stash_failed(r: G.GitResult, tree: Path) -> bool:
    """Is this git's own `fatal: stash failed`, the pre-merge save of the dirty tree? Its whole
    answer is on stderr (with the lock explanation before it), the last line that sentence,
    NOTHING on stdout (a conflict reports there) and no indented line (git lists files
    indented, so a refusal naming files is not it, whatever a file is NAMED). Git saves state
    BEFORE it merges anything, so a merge already begun (MERGE_HEAD) is never this."""
    lines = [ln for ln in r.err.splitlines() if ln.strip()]
    if r.out.strip() or not lines or lines[-1] != "fatal: stash failed":
        return False
    if any(ln[0].isspace() for ln in lines):
        return False
    probe = _W.git(tree, "rev-parse", "-q", "--verify", "MERGE_HEAD")
    return not probe.ok and not probe.unavailable  # "could not run" is not "no merge begun"


def _merge_msg_missing(r: G.GitResult, tree: Path) -> bool:
    """Is this git's `fatal: Could not read from .git/MERGE_MSG: ...`? The merge-message
    file vanished as git read it (a concurrent process in the same tree mid-merge), so —
    like ``fatal: stash failed`` — nothing was merged and the identical retry succeeds.
    Anchored to git's own error line, never the bare phrase; a merge already begun
    (MERGE_HEAD) is never this, whatever a file is NAMED."""
    lines = [ln for ln in r.err.splitlines() if ln.strip()]
    if r.out.strip() or not lines:
        return False
    if any(ln[0].isspace() for ln in lines):
        return False
    if not any(ln.startswith("fatal: Could not read from .git/MERGE_MSG") for ln in lines):
        return False
    probe = _W.git(tree, "rev-parse", "-q", "--verify", "MERGE_HEAD")
    return not probe.ok and not probe.unavailable  # "could not run" is not "no merge begun"


def _transient(r: G.GitResult, tree: Path) -> bool:
    """A transient pre-merge failure that merged nothing: retry the identical command."""
    return _stash_failed(r, tree) or _merge_msg_missing(r, tree)


def _overwritten(r: G.GitResult) -> bool:
    """Is this git's own refusal to merge over a locally-modified tracked file it would
    overwrite? Unlike ``fatal: stash failed`` (its pre-merge save died on a lock), this is
    the dirty tree itself refusing the merge, and the branch was never judged. Anchored to
    the refusal LINE, never the bare phrase: a conflict over a file literally NAMED
    "would be overwritten by merge" prints the phrase inside a CONFLICT line, and git does
    not start those with ``error: ``."""
    for line in (r.err or r.out).splitlines():
        if line.strip().startswith("error: ") and line.strip().endswith(
            "would be overwritten by merge:"
        ):
            return True
    return False


def _merge_retrying_stash(tree: Path, args: list[str]) -> G.GitResult:
    """`git <args>`, retried only while git reports a transient pre-merge failure that
    merged nothing (`stash failed` or a lost `MERGE_MSG` read). When it never clears, the
    error names the cause and says nothing was merged."""
    r = _W.git(tree, *args)
    tries = 1
    while not r.ok and _transient(r, tree) and tries < STASH_RETRIES:
        _W.time.sleep(STASH_RETRY_PAUSE_S * 2 ** (tries - 1))
        r = _W.git(tree, *args)
        tries += 1
    if not r.ok and _stash_failed(r, tree):
        return G.GitResult(
            r.code,
            r.out,
            f"{r.err or r.out}\n\ngit could not save the uncommitted state of {tree} before "
            f"merging, {tries} times (usually another process holding the git `index.lock` of {tree} "
            "or rewriting a tracked file as git read it); nothing was merged. Retry the merge "
            "once that process is done, or commit or move the uncommitted changes.",
            attempted=False,
        )
    if not r.ok and _merge_msg_missing(r, tree):
        return G.GitResult(
            r.code,
            r.out,
            f"{r.err or r.out}\n\ngit could not read {tree}/.git/MERGE_MSG while merging, "
            f"{tries} times (usually another process in {tree} mid-merge); nothing was merged. "
            "Retry the merge once that process is done.",
            attempted=False,
        )
    if not r.ok and _overwritten(r):
        # git names the exact files the merge would overwrite; that is a dirty tree, not a
        # judgement on the branch, so it is refused, never recorded as a failed merge.
        return G.GitResult(
            GIT_REFUSED,
            r.out,
            f"{r.err or r.out}\n\nNothing was merged (the item's branch was not judged). "
            f"Commit or move those uncommitted changes, then re-run the merge.",
            attempted=False,
        )
    return r

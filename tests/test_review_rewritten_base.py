"""B05dce361fd: `critic`/`rubber_duck` must review the item's OWN work after the base
branch's history was rewritten, not a huge reversed diff against the rewritten base.

A secret scrub (`filter-repo` or a force-pushed `--amend` chain) replaces the commit a
worktree forked from. `git merge-base main HEAD` then lands thousands of commits back --
or finds nothing -- and the old code diffed `main..HEAD`, sending the scrub's reverse
changes to the reviewer as if the item made them (21 MB in the repro). Fix: the fork's
SHA is recorded when the worktree is created (`Worktree.base_sha`), and `diff_for` diffs
against it when the live base no longer contains that SHA (the base was rewritten).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git as _git

import ddflow.api.review as api
from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog


def _configured(repo: Path) -> None:
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text("[worktree]\nenabled = false\n")


def test_worktree_create_records_the_base_sha_it_forked_from(repo: Path) -> None:
    from ddflow.infra import worktree as W

    cfg = Config.load(repo, env={})
    fork = _git(repo, "rev-parse", "main")
    wt = W.create(repo, cfg, "T1")  # base defaults to the default branch
    assert wt.base_sha == fork
    assert wt.base == "main"


def test_diff_for_uses_the_fork_sha_when_the_base_was_rewritten(repo: Path) -> None:
    # main's own content, then a feature branch whose only change is work.py.
    _configured(repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    (repo / "base.py").write_text("base = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    fork = _git(repo, "rev-parse", "main")
    run_cli(repo, "task", "add", "T1", "--title", "add work", "--globs", "*.py")
    _git(repo, "checkout", "-q", "-b", "feat")
    (repo / "work.py").write_text("work = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "work")

    # The fork sha the worktree records at claim time (what `worktree.created` carries).
    EventLog(repo, "test").append(
        "worktree.created", "T1", {"path": "", "branch": "feat", "base": "main", "base_sha": fork}
    )

    # Rewrite main (a scrub): amend the base commit, so `fork` is no longer on main and
    # `merge-base main feat` falls back to the commit before it.
    _git(repo, "checkout", "-q", "main")
    (repo / "base.py").write_text("base = 2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--amend", "-m", "base, scrubbed")

    st = fold(EventLog(repo).read_all(), strict=False)
    cfg = Config.load(repo, env={})
    diff, how = api.diff_for(repo, cfg, st, "T1", branch="feat")

    # The item's own work -- and NOT the base's (pre-scrub) content it did not touch.
    assert "work.py" in diff
    assert "base.py" not in diff, "the rewritten base's content leaked into the review diff"
    assert "rewritten" in how, how


def test_claim_persists_the_fork_sha_on_the_item(repo: Path) -> None:
    """The end-to-end path the bug depends on: claim writes `worktree.created` with the
    fork sha, and folding it lands on the item (not just the in-memory Worktree)."""
    run_cli(repo, "init")
    (repo / "base.py").write_text("base = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    fork = _git(repo, "rev-parse", "main")
    run_cli(repo, "task", "add", "T1", "--title", "add work", "--globs", "*.py")
    run_cli(repo, "claim", "T1", "--globs", "*.py")
    st = fold(EventLog(repo).read_all(), strict=False)
    assert st.items["T1"].base == "main"
    assert st.items["T1"].base_sha == fork


def test_a_branch_rebased_onto_the_rewritten_base_is_not_diffed_against_the_stale_fork(
    repo: Path,
) -> None:
    # Same rewrite, but the branch is then rebased onto the new main: the old fork sha is
    # no longer an ancestor of the branch, so it must NOT be substituted -- the live base
    # now shares recent history again and is the right fork.
    _configured(repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    (repo / "base.py").write_text("base = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    fork = _git(repo, "rev-parse", "main")
    run_cli(repo, "task", "add", "T1", "--title", "add work", "--globs", "*.py")
    _git(repo, "checkout", "-q", "-b", "feat")
    (repo / "work.py").write_text("work = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "work")

    EventLog(repo, "test").append(
        "worktree.created", "T1", {"path": "", "branch": "feat", "base": "main", "base_sha": fork}
    )
    # Rewrite main, then rebase feat onto it: the old fork is orphaned.
    _git(repo, "checkout", "-q", "main")
    (repo / "base.py").write_text("base = 2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--amend", "-m", "base, scrubbed")
    _git(repo, "checkout", "-q", "feat")
    _git(repo, "rebase", "-q", "--onto", "main", fork)

    st = fold(EventLog(repo).read_all(), strict=False)
    cfg = Config.load(repo, env={})
    diff, how = api.diff_for(repo, cfg, st, "T1", branch="feat")

    assert "work.py" in diff
    assert "base.py" not in diff, "the stale fork leaked the base's content into the diff"
    assert "rewritten" not in how


def test_a_branch_that_merged_the_rewritten_base_is_diffed_against_the_live_base(
    repo: Path,
) -> None:
    # A real rewrite (fork A orphaned off main) followed by a merge of the rewritten main:
    # the tip now CONTAINS the live base, so the stale fork must NOT be substituted --
    # `base...tip` is once more the right fork where the merge brought the base in.
    _configured(repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    (repo / "base.py").write_text("base = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    fork = _git(repo, "rev-parse", "main")
    run_cli(repo, "task", "add", "T1", "--title", "add work", "--globs", "*.py")
    _git(repo, "checkout", "-q", "-b", "feat")
    (repo / "work.py").write_text("work = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "work")

    EventLog(repo, "test").append(
        "worktree.created", "T1", {"path": "", "branch": "feat", "base": "main", "base_sha": fork}
    )
    # Move main to a sibling commit off its own parent (fork is orphaned), then merge it.
    _git(repo, "checkout", "-q", "main")
    _git(repo, "reset", "-q", "--hard", "HEAD~1")
    (repo / "scrub.py").write_text("scrubbed\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "scrub")
    _git(repo, "checkout", "-q", "feat")
    _git(repo, "merge", "-q", "main", "-m", "merge scrubbed main")

    st = fold(EventLog(repo).read_all(), strict=False)
    it = st.items["T1"]
    base, warning = api._base_after_rewrite(repo, it, "main", "feat")
    assert base == "main", "a tip that merged the rewritten base must fall back to the live base"
    assert warning == ""

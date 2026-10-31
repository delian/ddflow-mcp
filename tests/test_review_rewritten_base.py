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

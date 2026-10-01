"""`merge` never leaves the PRIMARY checkout mid-merge, and the lease hook passes the
merge commit `merge` itself makes.

B6926ec1ad9: a conflicting `ddflow merge` left the primary with MERGE_HEAD, staged files
and an unmerged path. Every later merge, by every agent, then failed ("you have unmerged
files"), `doctor` said Healthy and `recover` had nothing to recover; the agents are
rightly forbidden from touching the primary, so the queue stayed wedged until a person
ran `git merge --abort`.

B9b57176aac / Bc50bc7fb58: with the pre-commit framework, `ddflow-check-commit` (no
`stages:`) also runs at the commit-msg stage of `git merge`. It judged ddflow's own
merge commit against the leases of whoever the PRIMARY tree resolves to -- not the
holder (B9b5) -- from an event log the framework may have stashed (Bc50), refused it,
and left the primary mid-merge. A commit whose index is exactly the automatic merge of
its parents adds nothing that was not already committed, under a lease, on one of them.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3
ROOT = Path(__file__).resolve().parents[1]


def _git(where: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(where), *args], check=check, capture_output=True, text=True
    )


def _mid_merge(repo: Path) -> bool:
    return _git(repo, "rev-parse", "-q", "--verify", "MERGE_HEAD", check=False).returncode == 0


@pytest.fixture
def item(repo, monkeypatch):
    """T1 claimed by `alpha` into its own worktree; returns that tree."""
    monkeypatch.delenv("DDFLOW_AGENT", raising=False)
    run_cli(repo, "init")
    (repo / "c.txt").write_text("base\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "edit c", "--globs", "c.txt")
    code, out, err = run_cli(repo, "--json", "claim", "T1", agent="alpha")
    assert code == OK, out + err
    return Path(json.loads(out)["worktree"])


def test_a_conflicting_merge_leaves_the_primary_clean(repo, item):
    (item / "c.txt").write_text("branch\n")
    _git(item, "commit", "-qam", "branch edit")
    (repo / "c.txt").write_text("main\n")
    _git(repo, "commit", "-qam", "main edit")
    head = _git(repo, "rev-parse", "HEAD").stdout

    code, out, err = run_cli(repo, "merge", "T1", agent="alpha")
    assert code == REFUSED, out + err
    assert not _mid_merge(repo), "the primary was left mid-merge"
    assert _git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip() == ""
    assert _git(repo, "rev-parse", "HEAD").stdout == head
    # The remedy is the agent's to apply, in its own tree.
    assert "c.txt" in err and "into your branch" in err
    # And the next merge is not wedged: resolve on the branch, merge again.
    _git(item, "merge", "-q", "main", "-X", "ours", "-m", "take main")
    code, out, err = run_cli(repo, "merge", "T1", agent="alpha")
    assert code == OK, out + err


def _install_check_commit_as(repo: Path, name: str = "commit-msg") -> None:
    """What the pre-commit framework does with a `ddflow-check-commit` hook that names no
    `stages:` -- runs it at the commit-msg stage too, which `git merge` invokes (and at
    pre-merge-commit, where installed: there MERGE_HEAD is not written yet)."""
    hook = repo / ".git" / "hooks" / name
    hook.write_text(
        f"#!/bin/sh\nPYTHONPATH={ROOT} exec {sys.executable} -m ddflow hooks check-commit\n"
    )
    hook.chmod(0o755)
    run_cli(repo, "config", "--set", "enforce.commit_without_lease", "block")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "policy", "--no-verify")


@pytest.mark.parametrize("stage", ["commit-msg", "pre-merge-commit"])
def test_the_lease_hook_passes_ddflows_own_merge_commit(repo, item, stage):
    _install_check_commit_as(repo, stage)
    (item / "c.txt").write_text("branch\n")
    _git(item, "commit", "-qam", "branch edit", "--no-verify")

    # From the primary, as no one in particular: the hook resolves the primary's own
    # identity, which holds nothing -- the B9b57176aac situation.
    code, out, err = run_cli(repo, "merge", "T1")
    assert code == OK, out + err
    assert _git(repo, "show", "main:c.txt").stdout == "branch\n"
    assert not _mid_merge(repo)


def test_a_hand_made_merge_commit_is_still_checked(repo, item):
    _install_check_commit_as(repo)
    (item / "c.txt").write_text("branch\n")
    _git(item, "commit", "-qam", "branch edit", "--no-verify")

    _git(repo, "merge", "--no-commit", "--no-ff", "ddflow/T1")
    (repo / "extra.txt").write_text("nobody leased this\n")
    _git(repo, "add", "extra.txt")
    r = _git(repo, "commit", "-m", "merge plus extra", check=False)
    assert r.returncode != 0, "an edit riding on a merge commit escaped the lease check"
    assert "extra.txt" in r.stderr + r.stdout
    _git(repo, "merge", "--abort")


def test_doctor_flags_a_primary_left_mid_merge(repo, item):
    (item / "c.txt").write_text("branch\n")
    _git(item, "commit", "-qam", "branch edit")
    (repo / "c.txt").write_text("main\n")
    _git(repo, "commit", "-qam", "main edit")
    _git(repo, "merge", "ddflow/T1", check=False)  # by hand: conflicts, left mid-merge
    assert _mid_merge(repo)

    code, out, err = run_cli(repo, "doctor")
    assert code != OK, out + err
    assert "mid-merge" in out + err and "merge --abort" in out + err


def test_a_conflicting_squash_merge_leaves_the_primary_clean(repo, item):
    run_cli(repo, "config", "--set", "worktree.merge_strategy", "squash")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "squash", "--no-verify")
    (item / "c.txt").write_text("branch\n")
    _git(item, "commit", "-qam", "branch edit")
    (repo / "c.txt").write_text("main\n")
    _git(repo, "commit", "-qam", "main edit")

    code, out, err = run_cli(repo, "merge", "T1", agent="alpha")
    assert code == REFUSED, out + err
    assert _git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip() == ""


def _squash(repo: Path) -> None:
    run_cli(repo, "config", "--set", "worktree.merge_strategy", "squash")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "squash", "--no-verify")


def test_the_lease_hook_passes_ddflows_own_squash_commit(repo, item):
    # A squash concludes with a plain `git commit`: the pre-commit stage, no MERGE_HEAD.
    _install_check_commit_as(repo, "pre-commit")
    _squash(repo)
    (item / "c.txt").write_text("branch\n")
    _git(item, "commit", "-qam", "branch edit", "--no-verify")

    code, out, err = run_cli(repo, "merge", "T1")
    assert code == OK, out + err
    assert _git(repo, "show", "main:c.txt").stdout == "branch\n"


def test_a_refused_squash_commit_is_unstaged(repo, item):
    _squash(repo)
    (item / "c.txt").write_text("branch\n")
    _git(item, "commit", "-qam", "branch edit")
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho refused-by-test >&2\nexit 1\n")
    hook.chmod(0o755)

    code, out, err = run_cli(repo, "merge", "T1", agent="alpha")
    assert code == REFUSED, out + err
    assert "refused-by-test" in err
    assert _git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip() == ""

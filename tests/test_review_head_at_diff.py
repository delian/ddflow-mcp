"""B4f5be8179f / B99e47e0390: a review records as its `reviewed_head` the head whose diff
it took, not the head the branch has when the reviewer finally answers.

A review takes minutes; an author keeps committing meanwhile. Recording the head at the
END counted those commits as reviewed, and the next `--delta` said "nothing changed since
the reviewed head" -- unreviewed commits passing the delta check. Both a full round and a
delta round must capture the head with the diff. The reviewer here is a shell script that
commits to the branch while it "reviews", once per armed marker file.

That reviewer has a side effect, so it runs with `hedge = 1` (B10034dff26 / Ba1ac2327a4),
one copy per request. Hedged (the default: 2 copies), the copies race; the copy that finds
the marker already taken answers first, and the winner's cancel kills the copy that was
committing -- under load, a review with no late commit, and a test that flaked one run in six.
"""

from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

import ddflow.api.review as api
from ddflow.core.model import fold
from ddflow.infra.log import EventLog

OK, REFUSED = 0, 3


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _setup(repo: Path, tmp_path: Path) -> Path:
    """A project whose reviewer, when `arm` exists, commits `late<N>.py` to the checked-out
    branch mid-review (and disarms). Returns the marker path."""
    arm = tmp_path / "arm"
    cli = tmp_path / "fake-reviewer"
    calls = tmp_path / "calls"
    cli.write_text(
        f"#!/bin/sh\necho x >> '{calls}'\ncat >/dev/null\n"
        f"if [ -f '{arm}' ]; then n=$(cat '{arm}'); rm -f '{arm}'; "
        f"printf 'late = %s\\n' \"$n\" > '{repo}/late'$n'.py'; "
        f"git -C '{repo}' add 'late'$n'.py' >/dev/null; "
        f"git -C '{repo}' commit -qm \"late $n\" >/dev/null; fi\n"
        "echo 'STATUS: NO FINDINGS'\n"
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n"
        f'[[reviewer]]\nname = "fake"\nkind = "command"\ncommand = "{cli}"\n'
        'model = "gemini-2.5-pro"\ngates = ["critic"]\nhedge = 1\n'
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")
    _git(repo, "checkout", "-q", "-b", "feat")
    (repo / "x.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "work")
    return arm


def _review(repo: Path, **kw):
    return api.review(repo, gate="critic", item="T1", branch="feat", **kw)


def _ev(repo: Path) -> dict:
    return fold(EventLog(repo).read_all(), strict=False).items["T1"].gates["critic"].evidence


def test_the_side_effecting_reviewer_runs_once_per_review(repo, tmp_path):
    """The fixture's reviewer commits, so it must not be hedged: a second copy racing it
    can win and kill it mid-commit. One review, one invocation."""
    from ddflow.services import review as R

    arm = _setup(repo, tmp_path)
    assert [r.hedge for r in R.reviewers_for(R.load_reviewers(repo), "critic")] == [1]
    arm.write_text("1")
    assert _review(repo).exit == OK
    # The diff is one small chunk, so one request: any second call is a hedged copy.
    assert (tmp_path / "calls").read_text().count("x") == 1
    assert _git(repo, "ls-tree", "--name-only", "HEAD", "late1.py") == "late1.py", (
        "committed, uncancelled"
    )


def test_a_commit_made_while_a_full_review_runs_shows_up_in_the_next_delta(repo, tmp_path):
    arm = _setup(repo, tmp_path)
    taken = _git(repo, "rev-parse", "HEAD")
    arm.write_text("1")
    first = _review(repo)
    assert first.exit == OK, first.reason
    late = _git(repo, "rev-parse", "HEAD")
    assert late != taken, "the reviewer committed while it ran"
    assert _ev(repo)["reviewed_head"] == taken, "the head whose diff was reviewed"
    out = _review(repo, delta=True)
    assert out.exit == OK, out.reason
    assert "delta review of 1 commit since " + taken[:10] in out.data["text"], out.data["text"]
    assert _ev(repo)["reviewed_head"] == late


def test_a_commit_made_while_a_delta_round_runs_is_not_skipped(repo, tmp_path):
    """B99e47e0390's case: the same skip, one round later -- a DELTA round's record."""
    arm = _setup(repo, tmp_path)
    assert _review(repo).exit == OK
    (repo / "fix.py").write_text("ok = 1\n")
    _git(repo, "add", "fix.py")
    _git(repo, "commit", "-qm", "fix")
    taken = _git(repo, "rev-parse", "HEAD")
    arm.write_text("2")
    d1 = _review(repo, delta=True)
    assert d1.exit == OK, d1.reason
    assert _ev(repo)["reviewed_head"] == taken
    d2 = _review(repo, delta=True)
    assert d2.exit == OK, d2.reason
    assert "nothing changed" not in (d2.reason or "")
    assert "delta review of 1 commit since " + taken[:10] in d2.data["text"], d2.data["text"]

"""A change that is ENTIRELY excluded from review must still complete (bug
B-lockfile-only-item-cannot-complete).

A dependency refresh changed only uv.lock. [review].exclude_globs drops it, so the diff
reviewers are sent was empty and rubber_duck and critic recorded UNAVAILABLE; `complete`
then refused on reviewer independence ("no reviewer ran at all"), and uv.lock matches no
[chore].globs, so the item had no completable path without --force. The fix: exclusion
that would swallow the WHOLE diff sends the full diff instead -- a change made entirely of
excluded files has nothing else the exclusion could protect, and the review that runs is
real, so a cross-family reviewer can establish independence like any other change.
"""

from __future__ import annotations

import stat
from pathlib import Path

import ddflow.api.review as api
from ddflow.config import Config
from ddflow.services import review_exclude as REX

from conftest import run_cli


def _sec(path: str, body: str = "LOCKED") -> str:
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-a\n+{body}\n"


def test_exclusion_that_would_swallow_the_whole_diff_returns_the_full_diff(tmp_path):
    """REX.apply never returns an empty kept diff when exclusions matched everything."""
    say: list[str] = []
    kept, evidence, gone = REX.apply(_sec("uv.lock"), Config(), Path(tmp_path), say.append, {})
    assert "LOCKED" in kept and not gone.files
    assert any("uv.lock" in s for s in say)
    assert evidence.get("whole_diff_excluded") == ["uv.lock"]


def _setup_lock_only_change(repo: Path, tmp_path: Path) -> Path:
    seen = tmp_path / "seen.txt"
    cli = tmp_path / "fake-reviewer"
    cli.write_text(
        f"#!/bin/sh\nin=$(cat)\nprintf '%s\\n' \"$in\" >> '{seen}'\necho 'STATUS: NO FINDINGS'\n"
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n"
        f'[[reviewer]]\nname = "fake"\nkind = "command"\ncommand = "{cli}"\n'
        'model = "gemini-2.5-pro"\ngates = ["critic", "rubber_duck"]\n'
    )
    from helpers import git as _git

    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "refresh deps", "--globs", "*")
    (repo / "uv.lock").write_text('name = "ddflow"\nversion = "0.2.9"\n')
    return seen


def test_a_lockfile_only_change_is_reviewed_not_unavailable(repo, tmp_path):
    """The whole change is uv.lock (excluded by default): the review still runs on it."""
    seen = _setup_lock_only_change(repo, tmp_path)
    out = api.review(repo, gate="critic", item="T1")
    assert out.exit == 0, out.reason
    assert "uv.lock" in seen.read_text()
    assert "uv.lock" in out.data.get("text", "")


def test_a_lockfile_only_change_still_reviews_mixed_excluded_files(repo, tmp_path):
    """Snapshot AND lock, both excluded: the whole diff is still sent, not nothing."""
    seen = _setup_lock_only_change(repo, tmp_path)
    (repo / "tests" / "golden").mkdir(parents=True, exist_ok=True)
    (repo / "tests" / "golden" / "snap.json").write_text('{"GENERATED_MARK": 1}\n')
    out = api.review(repo, gate="critic", item="T1")
    assert out.exit == 0, out.reason
    assert "GENERATED_MARK" in seen.read_text()


def test_complete_accepts_a_lockfile_only_change(repo, tmp_path):
    """End to end: the lockfile-only item completes with a real review, no --force."""
    _setup_lock_only_change(repo, tmp_path)
    assert api.review(repo, gate="rubber_duck", item="T1").exit == 0
    assert api.review(repo, gate="critic", item="T1").exit == 0
    for gate in ("research", "rules", "implement", "standards", "ci", "unit_tests",
                 "bug_hunt", "dedupe", "merge"):
        code, out, err = run_cli(
            repo, "gate", "record", "T1", gate, "--outcome", "passed", "--evidence", "e",
            agent="a1",
        )  # fmt: skip
        assert code == 0, (gate, out, err)
    code, out, err = run_cli(repo, "complete", "T1", "--model", "claude-sonnet-5-5", agent="a1")
    assert code == 0, out + err

"""A change that is ENTIRELY excluded from review must still complete, and excluded
content is NEVER sent to a reviewer (bug B-lockfile-only-item-cannot-complete).

A dependency refresh changed only uv.lock. [review].exclude_globs drops it, so the diff
reviewers are sent was empty and rubber_duck and critic recorded UNAVAILABLE; `complete`
then refused on reviewer independence ("no reviewer ran at all"), and uv.lock matches no
[chore].globs, so the item had no completable path without --force. The fix does NOT
re-include excluded content (an exclusion may protect a secret, not noise): the review
stays UNAVAILABLE and names the excluded files, and a change made entirely of excluded
files needs no cross-family reviewer, the way a docs-only change needs none.
"""

from __future__ import annotations

import stat
from pathlib import Path

from conftest import run_cli

import ddflow.api.review as api
from ddflow.config import Config
from ddflow.services import review_exclude as REX


def test_excluded_outright_is_true_only_for_an_entirely_excluded_change(tmp_path):
    cfg = Config()
    assert REX.excluded_outright(cfg, Path(tmp_path), ["uv.lock"])
    assert not REX.excluded_outright(cfg, Path(tmp_path), ["uv.lock", "src/a.py"])
    assert not REX.excluded_outright(cfg, Path(tmp_path), [])
    cfg.review.exclude_globs = []
    assert not REX.excluded_outright(cfg, Path(tmp_path), ["uv.lock"])
    # the SAME inputs as the review that will run: an ad-hoc --exclude is what decides
    assert REX.excluded_outright(cfg, Path(tmp_path), ["server.pem"], exclude="*.pem")
    assert not REX.excluded_outright(cfg, Path(tmp_path), ["server.pem"])


def _setup_lock_only_change(repo: Path, tmp_path: Path, mixed: bool = False) -> Path:
    seen = tmp_path / "seen.txt"
    cli = tmp_path / "fake-reviewer"
    cli.write_text(
        f"#!/bin/sh\nin=$(cat)\nprintf '%s\\n' \"$in\" >> '{seen}'\necho 'STATUS: NO FINDINGS'\n"
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    from helpers import git as _git

    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        f'[[reviewer]]\nname = "fake"\nkind = "command"\ncommand = "{cli}"\n'
        'model = "gemini-2.5-pro"\ngates = ["critic", "rubber_duck"]\n'
        '[gates]\ntask_pipeline = ["research", "rules", "implement", "rubber_duck",'
        ' "critic", "standards", "unit_tests", "bug_hunt", "dedupe", "merge"]\n'
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "refresh deps", "--globs", "*")
    (repo / "uv.lock").write_text('name = "ddflow"\nversion = "0.2.9"\nSECRET_ISH\n')
    if mixed:
        (repo / "x.py").write_text("x = 1\n")
    # The change lands in a commit citing T1 (as a plain git merge would): the item's
    # actual diff is readable, as it is for the landed-commit style the completion judges.
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "T1: refresh the lock")
    return seen


def _pipeline_gates() -> tuple[str, ...]:
    return (
        "research",
        "rules",
        "implement",
        "standards",
        "unit_tests",
        "bug_hunt",
        "dedupe",
        "merge",
    )


def test_a_lockfile_only_change_is_unavailable_not_leaked(repo, tmp_path):
    """The whole change is uv.lock (excluded by default): the review records UNAVAILABLE
    and the excluded content never reaches the reviewer."""
    seen = _setup_lock_only_change(repo, tmp_path)
    out = api.review(repo, gate="critic", item="T1")
    assert out.exit == 2
    sent = seen.read_text() if seen.exists() else ""
    assert "SECRET_ISH" not in sent


def test_a_mixed_change_still_excludes_the_lock(repo, tmp_path):
    """One real file beside the lock sends ONLY the real file: exclusion stays effective."""
    seen = _setup_lock_only_change(repo, tmp_path, mixed=True)
    out = api.review(repo, gate="critic", item="T1")
    assert out.exit == 0, out.reason
    sent = seen.read_text()
    assert "x = 1" in sent and "SECRET_ISH" not in sent


def test_complete_accepts_a_lockfile_only_change(repo, tmp_path):
    """End to end: reviews are UNAVAILABLE (named), no cross-family reviewer is demanded,
    and the lockfile-only item completes with no --force."""
    _setup_lock_only_change(repo, tmp_path)
    assert api.review(repo, gate="rubber_duck", item="T1").exit == 2
    assert api.review(repo, gate="critic", item="T1").exit == 2
    for gate in _pipeline_gates():
        code, out, err = run_cli(
            repo, "gate", "record", "T1", gate, "--outcome", "passed", "--evidence", "e",
            agent="a1",
        )  # fmt: skip
        assert code == 0, (gate, out, err)
    code, out, err = run_cli(repo, "complete", "T1", "--model", "claude-sonnet-5-5", agent="a1")
    assert code == 0, out + err


def test_complete_still_demands_a_reviewer_when_the_change_is_not_all_excluded(repo, tmp_path):
    """A mixed change (the lock and one code file) with no review recorded still refuses
    on independence: the exemption covers only a change whose WHOLE diff is excluded."""
    _setup_lock_only_change(repo, tmp_path, mixed=True)
    (repo / ".ddflow" / "config.toml").write_text("[worktree]\nenabled = false\n")
    for gate in ("rubber_duck", "critic"):
        assert api.review(repo, gate=gate, item="T1").exit == 2
    for gate in _pipeline_gates():
        run_cli(
            repo, "gate", "record", "T1", gate, "--outcome", "passed", "--evidence", "e",
            agent="a1",
        )  # fmt: skip
    code, out, err = run_cli(repo, "complete", "T1", "--model", "claude-sonnet-5-5", agent="a1")
    assert code != 0 and "independence" in out + err, out + err

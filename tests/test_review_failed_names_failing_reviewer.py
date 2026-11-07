"""A failed review names the reviewer that found the blocking defect (bug B3113b1f522).

With several reviewers in one round, `recorded <item>.<gate> = failed (reviewer X)` used to
name whichever reviewer `_identifying_reviewer` ranked as the identifying pass -- the first
reviewer that ran, or a clean cross-family one -- even when a DIFFERENT reviewer is the one
whose finding failed the gate. A clean cross-family reviewer (or the author's own family,
listed first) reporting zero findings was recorded as the reviewer that failed the gate,
hiding whose finding actually blocked it.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git as _git

import ddflow.api.review as api
from ddflow.core.model import fold
from ddflow.infra.log import EventLog


def _clean(path: Path) -> str:
    path.write_text("#!/bin/sh\ncat > /dev/null\necho 'STATUS: NO FINDINGS'\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def _failing(path: Path) -> str:
    path.write_text(
        "#!/bin/sh\ncat > /dev/null\n"
        "echo 'FINDING HIGH x.py:1'\necho 'this change is wrong'\n"
        "echo 'STATUS: FINDINGS 1'\n"
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def _setup(repo: Path, tmp_path: Path) -> None:
    """A deepseek author whose critic gate goes to a clean cross-family reviewer (google,
    listed first) and a finding-holder (xai, listed second). The gate fails on the xai
    finding; the recorded identifying reviewer must be xai, not the clean google peer."""
    clean = _clean(tmp_path / "clean")
    failing = _failing(tmp_path / "failing")
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n"
        '[agent]\nfamily = "deepseek"\n'
        f'[[reviewer]]\nname = "clean"\nkind = "command"\ncommand = "{clean}"\n'
        'model = "gemini-2.5-pro"\nfamily = "google"\ngates = ["critic"]\n'
        f'[[reviewer]]\nname = "failing"\nkind = "command"\ncommand = "{failing}"\n'
        'model = "grok-4.7"\nfamily = "xai"\ngates = ["critic"]\n'
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")
    _git(repo, "checkout", "-q", "-b", "feat")
    (repo / "x.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "work")


def _ev(repo: Path) -> dict:
    return fold(EventLog(repo).read_all(), strict=False).items["T1"].gates["critic"].evidence


def test_a_failed_review_names_the_reviewer_that_found_the_blocking_defect(repo, tmp_path):
    _setup(repo, tmp_path)
    out = api.review(repo, gate="critic", item="T1", branch="feat")
    assert out.exit == 0, out.data  # REVIEWED (completed), the verdict is `failed` in outcome
    assert out.data["outcome"] == "failed", out.data
    ev = _ev(repo)
    assert ev["reviewer"] == "failing", ev
    assert ev["family"] == "xai", ev


def test_naming_a_partial_failing_reviewer_keeps_the_clean_identifying_pass():
    """A PARTIAL reviewer that found the blocking defect is the named reviewer, but the
    identifying pass (whose completion drives the exit code) stays the clean REVIEWED peer:
    naming the failing reviewer must not flip a completed run's exit to REFUSED."""
    from types import SimpleNamespace as NS

    from ddflow.api.review.run import _failing_reviewer
    from ddflow.config import Config
    from ddflow.services import review as RV

    finding = RV.Finding(severity="HIGH", title="bad")
    clean = NS(reviewer="clean", status=RV.REVIEWED, findings=[])
    partial = NS(reviewer="partial", status=RV.PARTIAL, findings=[finding])
    cfg = Config.load(None, env={})
    named = _failing_reviewer([clean, partial], None, "critic", False, cfg, clean)
    assert named is partial, named
    assert clean.status == RV.REVIEWED
